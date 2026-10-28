"""shape.py — 纯函数层：数据进，数据出。零 I/O。

与 render.py 同一条边界（两者都在 `tests/test_purity.py` 的守卫里）：

    shape.py    数据整形：筛选、去重、派生列、拼装记录。
    render.py   文本渲染：数据 → Markdown / HTML / 人读行。
    cli.py      壳：读配置、发请求、写库、打印、决定退出码。

为什么整形要单独一层：这些规则（哪些算本周、哪条快照最新、派生列怎么算）
是 mpdt 的口径本身。它们不该藏在"顺便还发了个 HTTP 请求"的函数里 —— 那样
想验证"最近 30 天怎么筛"就得先起一个假 CDP。
"""

from __future__ import annotations

import datetime as dt
import email
import json
from email.header import decode_header, make_header
from typing import Any


def select_recent_articles(articles: list[Any], cutoff: str) -> list[Any]:
    """发表记录 → 去重后的目标列表 [(appmsgid_str, itemidx_int, publish_date)]。

    规则：发表日缺失或早于 cutoff 的丢掉；同一 (appmsgid, itemidx) 只留第一次出现。
    顺序保持输入顺序（上游按发表时间倒序），因为抓取顺序影响日志可读性。
    """
    seen: set = set()
    targets: list = []
    for a in articles:
        pub = a.get("publish_date")
        if not pub or pub < cutoff:
            continue
        key = (str(a["appmsgid"]), int(a["itemidx"]))
        if key in seen:
            continue
        seen.add(key)
        targets.append((key[0], key[1], pub))
    return targets


def detail_metrics(d: dict[str, Any], all_scene: int) -> dict[str, Any]:
    """单篇抓取结果 → 入库 metrics（合并渠道构成 + 派生列 total_read_user）。

    `all_scene` 是「全部」渠道的 scene 码（由 fetcher 层提供，这里不 import 它，
    免得纯函数层依赖抓取层）。
    """
    metrics = dict(d["metrics"])
    metrics.update(
        {
            "publish_date": d["publish_date"],
            "window_days": d["window_days"],
            "total_read_user": sum(
                rr["read_user"] for rr in d["channels"] if rr["scene"] == all_scene
            ),
            "channel_breakdown": d["channel_breakdown"],
        }
    )
    return metrics


def detail_view(d: dict[str, Any], metrics: dict[str, Any]) -> tuple[dict, str]:
    """一篇的展示视图 → (摘要字段, 一行人读文本)。

    摘要字段进 --json 的 items[]；文本进进度输出。放一起是因为两者读同一批
    渠道数据 —— 分开算会出现"摘要说 45%、屏幕说 44%"这种不一致。
    """
    breakdown = d.get("channel_breakdown") or []
    top = breakdown[0] if breakdown else {}
    rec = next((rr for rr in breakdown if rr.get("scene_label") == "推荐"), {})
    ratio = metrics.get("finished_read_pv_ratio")
    ratio_txt = f"{100 * ratio:.1f}%" if isinstance(ratio, (int, float)) else "NA"
    summary = {
        "appmsgid": d["appmsgid"],
        "itemidx": d["itemidx"],
        "title": d["title"],
        "publish_date": d["publish_date"],
        "read_uv": metrics.get("read_uv"),
        "finished_read_pv_ratio": ratio,
        "top_channel": top.get("scene_label"),
        "top_channel_pct": top.get("read_pct"),
        "recommend_pct": rec.get("read_pct"),
    }
    line = (
        f"  ✓ {d['appmsgid']}_{d['itemidx']} 阅读{metrics.get('read_uv')} 完读率{ratio_txt} "
        f"推荐{rec.get('read_pct', 'NA')}% "
        f"首渠道={top.get('scene_label', 'NA')}({top.get('read_pct', 'NA')}%)"
    )
    return summary, line


# ───────────────────────── 导出（CSV / JSON 同一套列定义） ─────────────────────────


def export_columns(rows: list[Any]) -> list[str]:
    """所有行 metrics 键的并集，排序 —— CSV 与 JSON 用同一份列序。

    排序而不是按出现顺序：同一份数据两次导出必须得到同样的文件，否则 diff
    工具和下游缓存全部失效。
    """
    return sorted({k for _sd, _am, _ix, _ti, m in rows for k in json.loads(m)})


def export_matrix(rows: list[Any], columns: list[str]) -> list[list]:
    """CSV 版行：stat_date/appmsgid/itemidx/title + 指标值（缺列填空串）。"""
    return [
        [sd, am, ix, ti] + [json.loads(m).get(k, "") for k in columns] for sd, am, ix, ti, m in rows
    ]


def export_records(rows: list[Any], columns: list[str]) -> list[dict]:
    """JSON 版行：与 CSV 同列，但保留原始类型（不填空串）。"""
    records = []
    for sd, am, ix, ti, m in rows:
        rec: dict = {"stat_date": sd, "appmsgid": am, "itemidx": ix, "title": ti}
        for k in columns:
            mm = json.loads(m)
            if k in mm:
                rec[k] = mm[k]
        records.append(rec)
    return records


def export_payload(
    source: str,
    columns: list[str],
    records: list[dict],
    generated_at: str,
    metrics_meta: dict | None = None,
) -> dict[str, Any]:
    """JSON 导出载荷（纯）：generated_at 由壳传入，本函数不读时钟。"""
    payload = {
        "source": source,
        "generated_at": generated_at,
        "columns": ["stat_date", "appmsgid", "itemidx", "title", *columns],
        "row_count": len(records),
        "records": records,
    }
    if metrics_meta is not None:
        payload["metrics_meta"] = metrics_meta
    return payload


def to_advice_rows(raw_rows: list, only_dates: set | None = None) -> list:
    """快照原始行 → 诊断行（纯）。

    取每个 (appmsgid, itemidx) 的**最新** stat_date 快照 —— 早期快照在新文章还没
    数据时是 read_uv=0，取早的会把好文章误判成"没量"。（R238 实测：10-19/10-20 的
    5 篇取早快照全是 0，取最新是 21/56/92/103。）

    `raw_rows` 是壳按 stat_date 升序取出的 (sd, title, metrics_json, appmsgid, itemidx)。
    """
    latest: dict = {}
    for sd, ti, m, am, ix in raw_rows:
        latest[(str(am), int(ix))] = (sd, ti, m)
    rows = []
    for (_am, _ix), (_sd, ti, m) in latest.items():
        d = json.loads(m)
        uv = d.get("read_uv")
        if not isinstance(uv, (int, float)):
            continue
        ch = {}
        for item in d.get("channel_breakdown") or []:
            ch[item.get("scene_label")] = item
        rec = (ch.get("推荐") or {}).get("read_user")
        msg = (ch.get("公众号消息") or {}).get("read_user")
        fin = d.get("finished_read_pv_ratio")
        pd = d.get("publish_date")
        if only_dates and pd not in only_dates:
            continue
        rows.append(
            {
                "date": pd,
                "title": ti or "",
                "read": uv,
                "rec": rec if isinstance(rec, (int, float)) else 0,
                "msg": msg if isinstance(msg, (int, float)) else 0,
                "fin": fin if isinstance(fin, (int, float)) else None,
                "form": None,
            }
        )
    rows.sort(key=lambda r: (r["date"] or "", -r["rec"]))
    return rows


def fetch_window(today, backfill: int | None, from_date: str | None, to_date: str | None):
    """本次拉取的 (begin, end) 日期区间（纯）。

    默认 T+1：昨天是最新可得日。--from/--to 显式覆盖，--backfill N 回补 N 天。
    """
    end = today - dt.timedelta(days=1)
    begin = today - dt.timedelta(days=backfill) if backfill else end
    if from_date:
        begin = dt.date.fromisoformat(from_date)
    if to_date:
        end = dt.date.fromisoformat(to_date)
    return begin, end


PUBLISH_METRIC_KEYS = ("read_num", "share_num", "like_num", "old_like_num", "comment_num")
DATACUBE_METRIC_KEYS = (
    "refdate",
    "finish_read_rate",
    "read_rate",
    "total_pv",
    "total_read_finish",
    "total_share",
    "send_succ",
)


def send_hour_of(create_time) -> str | None:
    """群发时刻（北京时间 HH:MM）；上游缺 create_time 时返回 None，不虚构。"""
    if not isinstance(create_time, int) or create_time <= 0:
        return None
    ts = dt.datetime.fromtimestamp(create_time, dt.timezone(dt.timedelta(hours=8)))
    return ts.strftime("%H:%M")


def publish_list_metrics(article: dict) -> dict:
    """发表列表一行的指标（纯）。R101：create_time/publish_date/send_hour 随行入库
    （C20 publish_window 消费链的 L1 缺口——解析层早有、入库曾丢弃）。"""
    ct = article.get("create_time")
    return {
        **{k: article.get(k) for k in PUBLISH_METRIC_KEYS},
        "create_time": ct if send_hour_of(ct) else None,
        "publish_date": article.get("publish_date") or None,
        "send_hour": send_hour_of(ct),
    }


def datacube_metrics(row: dict) -> dict:
    """datacube 一行的指标（纯）：只取本工具消费的键，上游多给的丢弃。"""
    return {k: row.get(k) for k in DATACUBE_METRIC_KEYS}


def mail_subject(raw) -> str:
    """IMAP 头字节 → 主题字符串（纯）。RFC2047 编码头在这里解码。"""
    if isinstance(raw, str):
        raw = raw.encode()
    hdr = email.message_from_bytes(raw)
    return str(make_header(decode_header(hdr["Subject"] or "")))


def subject_hit(subjects: list, needle: str) -> tuple:
    """按给定顺序找主题含 needle 的第一封 → (是否命中, 检查了几封)。"""
    for i, subj in enumerate(subjects, 1):
        if needle and needle in subj:
            return True, i
    return False, len(subjects)
