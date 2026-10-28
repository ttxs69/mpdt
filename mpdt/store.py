"""SQLite 快照（FR4）：(来源, 日期, appmsgid, itemidx) 幂等 upsert + journal 运行日志。
只追加式演进（schema_version），CSV 导出（附带口径元数据伴随文件）。"""

import csv
import datetime as dt
import json
import os
import sqlite3
from collections.abc import Sequence
from typing import Any

from . import metrics as metrics_mod
from . import shape

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  source TEXT NOT NULL,            -- publish_list | datacube_article | article_detail
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


def upsert(
    con: sqlite3.Connection,
    source: str,
    stat_date: str,
    appmsgid: str | int,
    itemidx: int | str,
    title: str,
    metrics: dict[str, Any],
    fetched_at: str | None = None,
) -> None:
    fetched_at = fetched_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(source, stat_date, appmsgid, itemidx) DO UPDATE SET "
        "title=excluded.title, metrics=excluded.metrics, fetched_at=excluded.fetched_at",
        (
            source,
            stat_date,
            str(appmsgid),
            int(itemidx),
            title,
            json.dumps(metrics, ensure_ascii=False),
            fetched_at,
        ),
    )


def log_run(con: sqlite3.Connection, cmd: str, ok: bool, detail: str = "") -> None:
    con.execute(
        "INSERT INTO journal(ran_at, cmd, ok, detail) VALUES (?,?,?,?)",
        (dt.datetime.now().astimezone().isoformat(timespec="seconds"), cmd, int(ok), detail),
    )
    con.commit()


def csv_metric_meta(source: str, keys: Sequence[str]) -> list[list[str]]:
    """CSV 各指标列的口径元数据行（源字段, 所属源, 窗口, 分母, 单位, 是否跨源可比）。

    口径唯一来源 = metrics.py 注册表（CONCEPTS / META_FIELDS / DEPRECATED）；
    未登记键（如上游新增字段）标注「(未登记)」。
    """
    out = []
    for k in keys:
        hit = metrics_mod.find_series(field=k, source=source)
        if hit:
            reg_key, _, s = hit
            # 找单位：CONCEPTS / META_FIELDS / DEPRECATED 任一张
            unit = "(未登记)"
            for table in (metrics_mod.CONCEPTS, metrics_mod.META_FIELDS, metrics_mod.DEPRECATED):
                if reg_key in table:
                    unit = table[reg_key].get("unit", "(未登记)")
                    break
            # 「是否跨源可比」列：指标概念才有意义（元数据/旧版填 '不适用'）
            kind = _meta_kind(reg_key)
            if kind:
                comparable = f"不适用（{kind}）"
            elif reg_key in metrics_mod.DEPRECATED:
                comparable = "不适用（已弃用）"
            else:
                comparable = "否" if _concept_series_count(reg_key) >= 2 else "是"
            out.append([k, s["source"], s["window"], s["denominator"], unit, comparable])
        else:
            out.append([k, source, "(未登记)", "(未登记)", "(未登记)", "(未登记)"])
    return out


def _snapshot_rows(con: sqlite3.Connection, source: str) -> list[Any]:
    """该 source 的全部快照（按 stat_date, appmsgid 排序）—— 两个导出共用。"""
    return con.execute(
        "SELECT stat_date, appmsgid, itemidx, title, metrics FROM snapshots "
        "WHERE source=? ORDER BY stat_date, appmsgid",
        (source,),
    ).fetchall()


def export_json(con: sqlite3.Connection, source: str, out_path: str, with_meta: bool = True) -> int:
    """导出指定 source 的快照 JSON（结构化下游友好版，列序与 CSV 版一致）。

    载荷：source / generated_at / columns（键并集，排序同 CSV）/ row_count /
    records（每行 stat_date+appmsgid+itemidx+title+指标键）/ metrics_meta
    （with_meta=True 时内嵌，行结构与 CSV 伴随文件 *.metrics.csv 完全一致）。
    """
    rows = _snapshot_rows(con, source)
    columns = shape.export_columns(rows)
    records = shape.export_records(rows, columns)
    meta = None
    if with_meta:
        meta = {
            "columns": [
                "column",
                "source",
                "window",
                "denominator",
                "unit",
                "cross_source_comparable",
            ],
            "rows": csv_metric_meta(source, columns),
        }
    payload = shape.export_payload(
        source,
        columns,
        records,
        dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        meta,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(rows)


def _concept_series_count(reg_key: str) -> int:
    """查询某个登记键（非 deprecated）下的 series 数。"""
    if reg_key in metrics_mod.CONCEPTS:
        return sum(
            1 for s in metrics_mod.CONCEPTS[reg_key]["series"].values() if not s.get("deprecated")
        )
    if reg_key in metrics_mod.META_FIELDS:
        return len(metrics_mod.META_FIELDS[reg_key]["series"])
    return 0


def _meta_kind(reg_key: str) -> str:
    """返回元数据键的 kind（snapshot/derived/structured）或 ''（指标概念）。"""
    if reg_key in metrics_mod.META_FIELDS:
        return str(metrics_mod.META_FIELDS[reg_key].get("kind", ""))
    return ""


def export_csv(con: sqlite3.Connection, source: str, out_path: str, with_meta: bool = True) -> int:
    """导出指定 source 的快照 CSV。

    with_meta=True（默认）时额外写伴随文件 `<out>.metrics.csv`：逐列口径元数据，
    防止混源导出后无从分辨列的源与窗口口径。
    """
    rows = _snapshot_rows(con, source)
    columns = shape.export_columns(rows)
    matrix = shape.export_matrix(rows, columns)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stat_date", "appmsgid", "itemidx", "title", *columns])
        w.writerows(matrix)
    if with_meta:
        meta_path = out_path + ".metrics.csv"
        with open(meta_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["column", "source", "window", "denominator", "unit", "cross_source_comparable"]
            )
            w.writerows(csv_metric_meta(source, columns))
    return len(rows)


def load_advice_source(con: sqlite3.Connection) -> list[Any]:
    """诊断层要的原始快照行（按 stat_date 升序）—— 只查询，不做整形。

    整形在 shape.to_advice_rows（纯）：同一条规则既服务周报也服务 CLI，
    不该长在 SQL 旁边。
    """
    return con.execute(
        "SELECT stat_date, title, metrics, appmsgid, itemidx FROM snapshots "
        "WHERE source='article_detail' ORDER BY stat_date"
    ).fetchall()
