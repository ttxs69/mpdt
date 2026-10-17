"""SQLite 快照（FR4）：(来源, 日期, appmsgid, itemidx) 幂等 upsert + journal 运行日志。
只追加式演进（schema_version），CSV 导出。"""
import csv
import datetime as dt
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  source TEXT NOT NULL,            -- publish_list | datacube_article | channel_page
  stat_date TEXT NOT NULL,         -- 快照口径日 YYYY-MM-DD
  appmsgid TEXT NOT NULL,
  itemidx INTEGER NOT NULL DEFAULT 1,
  title TEXT,
  metrics TEXT NOT NULL,           -- JSON（数值型指标，键名=上游字段名）
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (source, stat_date, appmsgid, itemidx)
);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT NOT NULL, cmd TEXT, ok INTEGER, detail TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    return con


def upsert(con, source: str, stat_date: str, appmsgid, itemidx, title, metrics: dict, fetched_at=None):
    fetched_at = fetched_at or dt.datetime.now().astimezone().isoformat(timespec='seconds')
    con.execute(
        "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(source, stat_date, appmsgid, itemidx) DO UPDATE SET "
        "title=excluded.title, metrics=excluded.metrics, fetched_at=excluded.fetched_at",
        (source, stat_date, str(appmsgid), int(itemidx), title,
         json.dumps(metrics, ensure_ascii=False), fetched_at))


def log_run(con, cmd: str, ok: bool, detail: str = ''):
    con.execute("INSERT INTO journal(ran_at, cmd, ok, detail) VALUES (?,?,?,?)",
                (dt.datetime.now().astimezone().isoformat(timespec='seconds'), cmd, int(ok), detail))
    con.commit()


def export_csv(con, source: str, out_path: str) -> int:
    rows = con.execute(
        "SELECT stat_date, appmsgid, itemidx, title, metrics FROM snapshots "
        "WHERE source=? ORDER BY stat_date, appmsgid", (source,)).fetchall()
    keys = sorted({k for _, _, _, _, m in rows for k in json.loads(m)})
    with open(out_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['stat_date', 'appmsgid', 'itemidx', 'title'] + keys)
        for sd, am, ix, ti, m in rows:
            mm = json.loads(m)
            w.writerow([sd, am, ix, ti] + [mm.get(k, '') for k in keys])
    return len(rows)
