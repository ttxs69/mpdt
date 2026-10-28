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

import json
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
