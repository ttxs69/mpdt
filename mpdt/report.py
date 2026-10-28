"""周报（FR5）—— 壳层。

FP core / imperative shell 的分界线：

    render.py   纯函数层：(数据) → (字符串 / 数据)。不查库、不写文件、不打印。
    report.py   壳：查库 → 调 render.build_report → 写文件 → 读回校验。

这个文件里只该出现三类操作：SQL 查询、文件读写、把两边接起来。任何"数字怎么算"
或"文字怎么写"都属于 render.py —— 那边拿一份 dict 就能验，不用造数据库。

为什么值得这样分：周报里每个数字都要能单独算给客户看（R4 口径说明）。纯函数层
可以针对一个手工构造的 dict 断言输出，壳层只需要一个真库冒烟。
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import metrics as metrics_mod
from . import render, shape, store

# ─────────────────────────── 查询（副作用：读库） ───────────────────────────


def latest_by_key(con: Any, source: str) -> dict[tuple[str, int], tuple[str, str, dict[str, Any]]]:
    """取 (source) 下每个 (appmsgid, itemidx) 的最新 stat_date 快照行 → dict。

    返回 {(appmsgid_str, itemidx_int): (stat_date, title, metrics_dict)}。
    """
    rows = con.execute(
        "SELECT appmsgid, itemidx, stat_date, title, metrics FROM snapshots "
        "WHERE source=? ORDER BY stat_date",
        (source,),
    ).fetchall()
    best = {}
    for am, ix, sd, ti, m in rows:
        best[(str(am), int(ix))] = (sd, ti, json.loads(m))  # 后写=更新快照日
    return best


def _week_article_keys(con: Any, week_begin: str, week_end: str) -> set[Any]:
    """本周**发表**的文章 key 集合 {(appmsgid, itemidx)}。

    R238：宽表（跨 series 对照）原来取全库最新快照 ⇒ 混进两周前的文章。
    现在按每篇文章自己的发表日过滤。发表日来源：ad30 与 pl 用 publish_date；
    dc7 用 refdate（20261007 或 2026-10-07）。
    """
    keys = set()
    for _src, am, ix, sd, m in con.execute(
        "SELECT source, appmsgid, itemidx, stat_date, metrics FROM snapshots"
    ).fetchall():
        decoded = render.decode_snapshot(sd, m)
        # 有发表日按发表日；无发表日字段的老数据按抓取日兜底，不丢数据
        if render.snapshot_in_week(sd, decoded, week_begin, week_end):
            keys.add((str(am), int(ix)))
    return keys


def _detail_in_week(con: Any, week_begin: str, week_end: str) -> list[Any]:
    """本周 article_detail 快照，按 (appmsgid, itemidx) 去重取最新，最多 15 条。"""
    det_map = {}
    for stat_date, title, raw, am, ix in con.execute(
        "SELECT stat_date, title, metrics, appmsgid, itemidx FROM snapshots "
        "WHERE source='article_detail' ORDER BY stat_date DESC, appmsgid DESC"
    ).fetchall():
        dm = render.decode_snapshot(stat_date, raw)
        if not render.snapshot_in_week(stat_date, dm, week_begin, week_end):
            continue
        k = (str(am), int(ix))
        if k not in det_map:  # rows 已按 stat_date DESC 排序 ⇒ 首个即最新
            det_map[k] = (stat_date, title, dm)
    return list(det_map.values())[:15]


def _series_cache(con: Any) -> dict[str, Any]:
    """每个用到的 source 一份「最新快照表」，宽表用。

    一次查全（原来每个概念、每条 series 各查一次），结果等价且少打 N 次库。
    """
    sources = {
        s["source"]
        for c in metrics_mod.CONCEPTS.values()
        for s in c["series"].values()
        if not s.get("deprecated")
    }
    return {src: latest_by_key(con, src) for src in sorted(sources)}


def collect(con: Any, week_begin: str, week_end: str) -> dict[str, Any]:
    """一次把周报要的所有原始数据查出来 —— 壳层唯一的"取数"入口。

    返回的 dict 是纯函数的入参，里面没有连接、没有游标、没有懒加载。
    """
    return {
        "detail_rows": con.execute(
            "SELECT stat_date, title, metrics FROM snapshots WHERE source='article_detail' "
            "ORDER BY stat_date"
        ).fetchall(),
        "dc_rows": con.execute(
            "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
            "ORDER BY stat_date"
        ).fetchall(),
        "series_by_source": _series_cache(con),
        "week_keys": _week_article_keys(con, week_begin, week_end),
        "detail_in_week": _detail_in_week(con, week_begin, week_end),
        "advice_rows": shape.to_advice_rows(store.load_advice_source(con)),
    }


def cross_source_table(
    con: Any, concept: str | None = None, week_keys: set | None = None
) -> list[dict]:
    """跨 series 对照（壳）：查一次最新快照表，交给 render 的纯函数排版。"""
    return render.cross_source_table(_series_cache(con), concept=concept, week_keys=week_keys)


# ─────────────────────────── 写文件（副作用：落盘） ───────────────────────────


def _write(out_dir: str, iso_week: str, md: str) -> tuple[str, str]:
    """写 md + html，返回两个路径。写后读回，确认落盘的字节与内存一致。"""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"weekly-{iso_week}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    html_path = os.path.join(out_dir, f"weekly-{iso_week}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render.build_html(iso_week, md))
    # 写后读回：确认落盘内容与内存里的一致，而不是"写调用没抛异常"
    with open(md_path, encoding="utf-8") as f:
        written_md = f.read()
    assert written_md == md and "口径说明" in written_md, "md 读回与内存不一致"
    # 注意空库周报无表格——<table> 存在性由带数据的测试覆盖，此处只断言不变量
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    assert (
        f"<title>mpdt 周报 {iso_week}</title>" in html
        and "<pre>" not in html
        and "口径说明" in html
    ), "HTML 模板化断言失败"
    return md_path, html_path


# ─────────────────────────── 编排 ───────────────────────────


def generate(con: Any, iso_week: str, out_dir: str) -> dict[str, Any]:
    """生成一周的周报：查库 → 纯函数算 + 排版 → 落盘。"""
    monday, sunday = render.week_bounds(iso_week)
    week_begin, week_end = monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    data = collect(con, week_begin, week_end)
    md, payload = render.build_report(iso_week, week_begin, week_end, data)
    md_path, html_path = _write(out_dir, iso_week, md)
    return {"md": md_path, "html": html_path, **payload}
