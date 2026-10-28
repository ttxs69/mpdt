"""render.py — 纯函数层（functional core）：数据进，字符串出（含邮件正文），零 I/O。

FP core / imperative shell 的分界就划在这里：

  - 本模块不 import os / sqlite3 / urllib，不打开文件、不打印、不读时钟。
  - 每个函数都是 (数据) → (数据 | 字符串)：同样的输入永远同样的输出。
  - report.py 是壳：查库、写文件、决定退出码；它调本模块，本模块不知道它存在。

为什么值得这样分：周报里每个数字都要能单独算给客户看。纯函数拿一份 dict 就能
验证输出，不需要造数据库、不需要起 Chrome、不需要落盘。
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import json
from email.message import EmailMessage
from itertools import combinations
from typing import Any, cast

from . import advice as advice_mod
from . import metrics as metrics_mod
from . import next_step as next_step_mod
from .metrics import calibration_note

_ANCHOR_PREFIX = "sec-"


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         max-width: 900px; margin: 24px auto; padding: 0 16px; color: #1a1a1a; line-height: 1.65; }}
  h1 {{ font-size: 1.5em; border-bottom: 2px solid #eee; padding-bottom: 8px; }}
  h2 {{ font-size: 1.25em; margin-top: 1.8em; }}
  h3 {{ font-size: 1.05em; margin-top: 1.4em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.92em;
           display: block; overflow-x: auto; }}  /* 小屏横向滚动而非挤压换行 */
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; white-space: nowrap; }}
  th {{ background: #f7f7f7; }}
  blockquote {{ margin: 12px 0; padding: 8px 14px; border-left: 4px solid #c8c8c8;
                background: #fafafa; color: #444; }}
  code {{ background: #f2f2f2; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
  p {{ margin: 0.6em 0; }}
  nav.toc {{ background: #fafafa; border: 1px solid #eee; border-radius: 6px;
             padding: 10px 14px; margin: 16px 0; font-size: 0.92em; }}
  nav.toc a {{ display: inline-block; margin: 3px 14px 3px 0; color: #0366d6;
               text-decoration: none; }}
  h2 {{ scroll-margin-top: 16px; }}  /* 锚点跳转留出呼吸空间 */
</style>
</head>
<body>
{body}
</body>
</html>
"""


def extract_toc(md: str) -> list[tuple[str, str]]:
    """从 md 提取 h2 目录（标题, anchor_id）。供 md_to_html 生成目录导航。"""
    toc = []
    i = 0
    for line in md.split("\n"):
        if line.startswith("## "):
            toc.append((line[3:].strip(), f"{_ANCHOR_PREFIX}{i}"))
            i += 1
    return toc


def inline_html(text: str) -> str:
    """行内 markdown → HTML：先转义，再 **加粗**、`行内码`。

    本函数只服务本模块自己生成的 md 子集（无嵌套、无链接），不追求通用。
    """
    import re

    text = html_mod.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def md_table_html(header_line: str, body_lines: list[str]) -> str:
    """| a | b | 形式的表格行 → <table>（首行表头；第二行是 --- 分隔线，调用方已剔除）。"""

    def cells(line: str) -> list[str]:
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        return parts

    h = cells(header_line)
    out = [
        "<table>",
        "<thead><tr>" + "".join(f"<th>{inline_html(c)}</th>" for c in h) + "</tr></thead>",
    ]
    out.append("<tbody>")
    for line in body_lines:
        cs = cells(line)
        # 列数对齐表头（多余截断、不足补空，防错位）
        cs = (cs + [""] * len(h))[: len(h)]
        out.append("<tr>" + "".join(f"<td>{inline_html(c)}</td>" for c in cs) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def table_starts_at(lines: list[str], i: int) -> bool:
    """当前行 + 下一行是否构成 Markdown 表格（表头行 + |--- 分隔行）。"""
    if not lines[i].strip().startswith("|") or i + 1 >= len(lines):
        return False
    sep = lines[i + 1].strip()
    return set(sep) <= {"|", "-", ":", " "} and "-" in sep


def heading_html(s: str, h2_index: int) -> tuple[str, int] | None:
    """标题行 → (html, 下一个 h2 序号)；不是标题返回 None。"""
    if s.startswith("### "):
        return f"<h3>{inline_html(s[4:])}</h3>", h2_index
    if s.startswith("## "):
        return f'<h2 id="{_ANCHOR_PREFIX}{h2_index}">{inline_html(s[3:])}</h2>', h2_index + 1
    if s.startswith("# "):
        return f"<h1>{inline_html(s[2:])}</h1>", h2_index
    return None


def blockquote_html(lines: list[str], i: int) -> tuple[str, int]:
    """连续 > 行合并为一个 blockquote → (html, 下一行号)。"""
    quoted = []
    n = len(lines)
    while i < n and lines[i].strip().startswith(">"):
        quoted.append(lines[i].strip().lstrip(">").strip())
        i += 1
    return "<blockquote>" + "<br>".join(inline_html(q) for q in quoted) + "</blockquote>", i


def md_to_html(md: str) -> str:
    """本报告 md 子集 → HTML 片段（标题/表格/引用/段落）+ 文首目录导航（锚点）。

    只处理 generate() 会产出的结构：#/##/### 标题、| 表格（含 --- 分隔行）、
    > 引用块、普通段落行。不支持列表/链接/嵌套——超出子集的行按段落渲染。
    h2 标题顺次编 id=sec-N，目录由 extract_toc 推导（两遍一致）。
    """
    lines = md.split("\n")
    out = []
    toc = extract_toc(md)
    if toc:
        nav = [
            '<nav class="toc">',
            "<b>目录</b>",
            "".join(f'<a href="#{a}">{inline_html(t)}</a>' for t, a in toc),
            "</nav>",
        ]
        out.append("\n".join(nav))
    h2_i = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if table_starts_at(lines, i):
            # 表格块：首行表头 + 分隔行 + 数据行（连续 | 开头）
            j = i + 2
            body = []
            while j < n and lines[j].strip().startswith("|"):
                body.append(lines[j])
                j += 1
            out.append(md_table_html(line, body))
            i = j
            continue
        heading = heading_html(s, h2_i)
        if heading is not None:
            html, h2_i = heading
            out.append(html)
        elif s.startswith(">"):
            html, i = blockquote_html(lines, i)
            out.append(html)
            continue
        else:
            out.append(f"<p>{inline_html(s)}</p>")
        i += 1
    return "\n".join(out)


def md_table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def fmt_value(v: Any, is_ratio: bool) -> str:
    """把数值格式化为人读串（ratio 概念 ×100%，其它按原值）。"""
    if not isinstance(v, (int, float)):
        return "-"
    if is_ratio:
        return f"{100 * v:.1f}%"
    return str(int(v)) if float(v).is_integer() else (f"{v:.1f}")


def week_bounds(iso_week: str) -> tuple[dt.date, dt.date]:
    year, week = iso_week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def publish_date(m: dict[str, Any]) -> str | None:
    """取文章自己的发表日：datacube 用 refdate(20261007 或 2026-10-07)，article_detail 用 publish_date。"""
    pd = m.get("publish_date")
    if pd and len(str(pd)) >= 10:
        return str(pd)[:10]
    rd = m.get("refdate")
    if rd:
        r = str(rd).strip()
        if len(r) == 8 and r.isdigit():
            return f"{r[:4]}-{r[4:6]}-{r[6:8]}"
        if len(r) >= 10:
            return r[:10]
    return None


def decode_snapshot(stat_date: str, raw: str) -> dict[str, Any]:
    """metrics 仍是 JSON 字符串时解析成 dict，并把抓取日挂成 _stat_date。"""
    d = json.loads(raw)
    d["_stat_date"] = stat_date
    return cast(dict[str, Any], d)


def snapshot_in_week(
    stat_date: str, decoded: dict[str, Any], week_begin: str, week_end: str
) -> bool:
    """是否属于本周：按文章自己的发表日判，缺发表日才退回抓取日。"""
    pd = publish_date(decoded)
    if pd is None:
        # 没有发表日字段的快照（旧数据 / 部分来源）：退回按抓取日 stat_date 判断，
        # 不能因为缺字段就把数据整段丢掉。
        return week_begin <= stat_date <= week_end
    return week_begin <= pd <= week_end


def week_snapshots(rows: list[Any], week_begin: str, week_end: str) -> list[Any]:
    """(stat_date, title, metrics_json) → 只留本周的 (stat_date, title, dict)。"""
    kept = []
    for stat_date, title, raw in rows:
        decoded = decode_snapshot(stat_date, raw)
        if snapshot_in_week(stat_date, decoded, week_begin, week_end):
            kept.append((stat_date, title, decoded))
    return kept


def is_fake_perfect(entry: tuple[Any, ...]) -> bool:
    """分母为 0 却被报成 100% 完读率的行。

    datacube：finish_read_rate>=1 且 total_read_finish==0 ⇒ 分母为 0。
    article_detail：完读率 1.0 但 read_uv 很小（<5 人）时，1 人读完 = 100%，无统计意义。
    """
    fr, m, src = entry
    try:
        if src == "dc7":
            return float(fr) >= 1.0 and int(m.get("total_read_finish") or 0) == 0
        return float(fr) >= 1.0 and int(m.get("read_uv") or 0) < 5
    except (TypeError, ValueError):
        return False


def top_row(kv: tuple[Any, ...], dc_by_art: dict[str, Any]) -> list[Any]:
    """Top 表的一行。阅读率（阅读/送达）只在 datacube 源有；该文章有 dc7 行时
    回填，否则留 '-'（不许用别的口径冒充）。"""
    title, _sd = kv[0]
    fr, m, src = kv[1]
    uv = m.get("read_uv")
    ch = m.get("channel_breakdown") or []
    rec = next((c for c in ch if c.get("scene_label") == "推荐"), None)
    rec_pct = rec.get("read_pct") if isinstance(rec, dict) else None
    avg = m.get("avg_article_read_time")
    rr = None
    dmrow = dc_by_art.get(title)
    if dmrow:
        rr = dmrow.get("read_rate")
    return [
        title[:36],
        f"{float(fr):.1%}",
        (uv if uv is not None else "-"),
        (f"{rec_pct:.0f}%" if isinstance(rec_pct, (int, float)) else "-"),
        (f"{avg:.0f}s" if isinstance(avg, (int, float)) else "-"),
        (f"{rr:.1%}" if isinstance(rr, (int, float)) else "-"),
        src,
    ]


def completion_candidates(arts: list[Any], dc_arts: list[Any]) -> dict[str, Any]:
    """按标题去重取最新快照；ad30 优先，dc7 只补 ad30 缺失的文章。

    「去重」= 同一 title 只留最新 stat_date 那一行：article_detail 是每日快照，
    同一篇文章会连续多天各一行（实测「rea 屠榜 GitHub」在 10-19/10-20/10-21 各一行），
    客户看到同标题重复出现会立刻质疑。
    """
    latest_by_art: dict[Any, Any] = {}
    for stat_date, title, m in arts:
        prev = latest_by_art.get(title)
        if prev is None or stat_date > prev[0]:
            latest_by_art[title] = (stat_date, m)
    by_key = {}
    for title, (stat_date, m) in latest_by_art.items():
        fr = m.get("finished_read_pv_ratio")
        if fr is not None:
            by_key[(title, stat_date)] = (fr, m, "ad30")
    dc_latest: dict[Any, Any] = {}
    dc_by_art = {}
    for stat_date, title, m in dc_arts:
        if title not in dc_latest or stat_date > dc_latest[title][0]:
            dc_latest[title] = (stat_date, m)
        dc_by_art[title] = m
    # 同一篇文章若 ad30 已有，则不再补 dc7（否则 Top 表里同一篇文章出现两行，
    # 客户会问「这篇为什么有两条」）。dc7 只用于 ad30 缺失的文章。
    have_arts = {title for (title, _sd) in by_key}
    for title, (stat_date, m) in dc_latest.items():
        if title in have_arts:
            continue
        if m.get("finish_read_rate"):
            by_key[(title, stat_date)] = (m["finish_read_rate"], m, "dc7")
    return {
        "by_key": by_key,
        "dc_by_art": dc_by_art,
        "articles": len(latest_by_art),
        "dc_articles": len(dc_latest),
    }


def completion_top(arts: list[Any], dc_arts: list[Any]) -> dict[str, Any]:
    """完读率 Top10：ad30 优先、dc7 兜底、剔除分母为 0 的伪 100%。"""
    cand = completion_candidates(arts, dc_arts)
    by_key = cand["by_key"]
    # R237：剔除「分母为 0 的伪 100%」。微信在 total_read_finish=0（没人读完）时
    # 把 finish_read_rate 返回 1（即 100%），而不是空值。直接排序会让这些行冲到
    # Top1 —— 实测本号 17 行里 2 行（11.8%）是这种。客户看到「完读率 100%」会立刻
    # 质疑，而这是分母为 0，不是内容好。
    kept = {k: v for k, v in by_key.items() if not is_fake_perfect(v)}
    top = sorted(kept.items(), key=lambda kv: -float(kv[1][0]))[:10]
    return {
        "rows": [top_row(kv, cand["dc_by_art"]) for kv in top],
        "n": min(10, len(top)),
        "articles": cand["articles"],
        "dc_articles": cand["dc_articles"],
        "fake_perfect": len(by_key) - len(kept),
    }


def ratio_diff_note(series: list[Any], by_source: dict[str, Any], key: tuple[str, int]) -> str:
    """比例概念的两两差值说明列（单位：百分点）。"""
    diffs = []
    for i in range(len(series)):
        for j in range(i + 1, len(series)):
            sa_key, sa = series[i]
            sb_key, sb = series[j]
            va = by_source[sa["source"]][key][2].get(sa["field"])
            vb = by_source[sb["source"]][key][2].get(sb["field"])
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                diffs.append(
                    (sa_key, sb_key, va - vb, "{} vs {}".format(sa["window"], sb["window"]))
                )
    return "；".join(f"{a}−{b}={100 * d:+.1f}pt" for a, b, d, _ in diffs)


def wide_row(
    key: tuple[str, int], series: list[Any], by_source: dict[str, Any], is_ratio: bool
) -> tuple[list[Any] | None, bool]:
    """宽表的一行 → (row | None, 扫描中是否见过非零值)。

    saw_nonzero 是**扫描语义**：先见到非零、再因缺字段被丢弃的行也算见过。
    `all_zero` 的原始口径就是这样算的，不能改成"只看保留下来的行"。
    返回 None 的两种情况：某个 series 缺该文章的快照/字段；整行数值全 0（折叠）。
    """
    _sd, title, _mm = by_source[series[0][1]["source"]].get(key, (None, "", {}))
    cells = []
    vals = []
    saw_nonzero = False
    for _skey, s in series:
        v = by_source[s["source"]].get(key, (None, "", {}))[2].get(s["field"])
        if not isinstance(v, (int, float)):
            return None, saw_nonzero
        vals.append(v)
        cells.append(fmt_value(v, is_ratio))
        if v != 0:
            saw_nonzero = True
    row = [(title or "")[:36], f"{key[0]}_{key[1]}", *cells]
    if is_ratio and len(series) >= 2:
        row.append(ratio_diff_note(series, by_source, key))
    # 「全 0」行折叠（噪音过滤）：宽表只保留至少一个 series 非零的行
    if any(v != 0 for v in vals):
        return row, saw_nonzero
    return None, saw_nonzero


def has_any_intersect_rows(
    series: list[Any], by_source: dict[str, Any], keys_union: set[Any]
) -> bool:
    """是否存在某个 key 在所有 series 源里都有数值（即宽表本可以有行）。"""
    for key in keys_union:
        values = [
            by_source[s["source"]].get(key, (None, "", {}))[2].get(s["field"]) for _k, s in series
        ]
        if all(isinstance(v, (int, float)) for v in values):
            return True
    return False


def folded_concept_lines(folded: list[dict[str, Any]]) -> list[Any]:
    """把「为什么折叠」按原因分组，每条一行。没有折叠就没有输出。"""
    out: list[Any] = []
    if not folded:
        return out
    missing_src = [x for x in folded if not x["has_data"] and x["missing_sources"]]
    missing_field = [
        x for x in folded if not x["has_data"] and not x["missing_sources"] and x["missing_fields"]
    ]
    no_intersect = [x for x in folded if x["no_intersect"]]
    all_zero_no_rows = [
        x
        for x in folded
        if x["all_zero"]
        and not x["has_data"]
        and not x["missing_sources"]
        and not x["missing_fields"]
    ]
    zero = [x for x in folded if x["has_data"] and x["all_zero"]]
    if missing_src:
        by_missing: dict[str, Any] = {}
        # 循环变量不能叫 b —— b 是周界（week begin），for 循环会覆盖它。
        # 旧代码用 b 且此后没再读 b，所以一直没炸；周筛延伸到本节之后才暴露。
        for blk in missing_src:
            key = "+".join(sorted(blk["missing_sources"]))
            by_missing.setdefault(key, []).append(blk["display"])
        parts = [
            "{}（缺 {} 快照）".format("、".join(names), miss) for miss, names in by_missing.items()
        ]
        out.append("")
        out.append("> **本周无可对照数据的概念**：{}。".format("；".join(parts)))
    if missing_field:
        by_mf: dict[str, Any] = {}
        for blk in missing_field:
            key = "+".join(sorted(blk["missing_fields"]))
            by_mf.setdefault(key, []).append(blk["display"])
        parts = ["{}（缺字段 {}）".format("、".join(names), mf) for mf, names in by_mf.items()]
        out.append("> **本周无可对照数据的概念**：{}。".format("；".join(parts)))
    if no_intersect:
        out.append(
            "> **本周无可对照数据的概念（快照 appmsgid 无交集）**：{}。".format(
                "、".join(x["display"] for x in no_intersect)
            )
        )
    if all_zero_no_rows:
        out.append(
            "> **本周宽表无信号（全 0 行）**：{}。".format(
                "、".join(x["display"] for x in all_zero_no_rows)
            )
        )
    if zero:
        out.append(
            "> **本周宽表无信号（全 0 概念）**：{}。".format("、".join(x["display"] for x in zero))
        )
    return out


def channel_pct(m: dict[str, Any], scene: int) -> object:
    """某个渠道（scene 码）的阅读占比；没有该渠道返回 None。"""
    for r in m.get("channel_breakdown") or []:
        if r.get("scene") == scene:
            return r.get("read_pct")
    return None


def channel_row(title: str, m: dict[str, Any]) -> list[Any] | None:
    """单篇渠道构成的一行；read_uv=0 的「无信号」行返回 None（跳过）。"""
    ruv = m.get("read_uv")
    if not isinstance(ruv, (int, float)) or ruv == 0:
        return None
    fr = m.get("finished_read_pv_ratio")
    return [
        (title or "")[:36],
        m.get("publish_date", ""),
        ruv,
        (f"{100 * fr:.1f}%") if isinstance(fr, (int, float)) else "-",
        m.get("avg_article_read_time", ""),
        channel_pct(m, 6) if channel_pct(m, 6) is not None else "-",
        channel_pct(m, 0) if channel_pct(m, 0) is not None else "-",
        channel_pct(m, 7) if channel_pct(m, 7) is not None else "-",
    ]


def wide_rows_for_concept(
    series_by_source: dict[str, Any], cname: str, week_keys: set[Any] | None = None
) -> dict[str, Any]:
    """某概念一张宽表 → {header, rows, has_data, all_zero, missing_sources, ...}。

    列 = [文章, appmsgid_itemidx, s1 值, s2 值, …, 差值说明列?]
    行 = 仅包含 (appmsgid, itemidx) 命中概念下**所有 series 源**都有快照的交集；
    且每个 series 字段都有数值。空交集 → has_data=False，周报层折叠。

    `series_by_source`：{source: {(appmsgid, itemidx): (stat_date, title, metrics)}}，
    由壳（report.py）一次查库备好 —— 本函数不碰数据库。
    """
    c: dict[str, Any] = metrics_mod.CONCEPTS[cname]
    series = [(k, s) for k, s in c["series"].items() if not s.get("deprecated")]
    if not series:
        return {
            "header": [],
            "rows": [],
            "has_data": False,
            "all_zero": False,
            "missing_sources": [],
        }
    is_ratio = "比例" in c["unit"]
    by_source = {s["source"]: series_by_source.get(s["source"], {}) for _, s in series}
    keys_union = set()
    for src_map in by_source.values():
        keys_union.update(src_map)
    if week_keys is not None:
        keys_union = {k for k in keys_union if k in week_keys}

    rows = []
    any_nonzero = False
    for key in sorted(keys_union):
        row, saw_nonzero = wide_row(key, series, by_source, is_ratio)
        any_nonzero = any_nonzero or saw_nonzero
        if row is not None:
            rows.append(row)

    header = ["文章", "appmsgid_itemidx"] + [
        f"{c['display']}({skey}·{s['window_days']}天)" for skey, s in series
    ]
    if is_ratio and len(series) >= 2:
        header.append("差值(百分点·两两差)")
    # 缺失源/字段诊断：哪些源完全没此概念的快照，或哪个 field 在该源任何快照都不存在
    missing_src = [sd["source"] for _, sd in series if not by_source[sd["source"]]]
    missing_field = []
    if rows == [] and not missing_src:
        for _skey, s in series:
            # values() 是 (sd, ti, mm) 三元组；mm 才是指标 dict
            if not any(s["field"] in row[2] for row in by_source[s["source"]].values()):
                missing_field.append(f"{s['source']}(`{s['field']}`)")
    # 单独追踪：all rows in 交集 all-zero（被「行级」过滤掉 → has_data 假但非"无源/无字段"）
    has_any_intersect = has_any_intersect_rows(series, by_source, keys_union)
    return {
        "header": header,
        "rows": rows,
        "has_data": bool(rows),
        "all_zero": bool(rows) and not any_nonzero,
        "missing_sources": missing_src,
        "missing_fields": missing_field,
        "all_rows_filtered": has_any_intersect
        and not rows
        and not missing_src
        and not missing_field,
        "no_intersect": (not has_any_intersect)
        and bool(keys_union)
        and not missing_src
        and not missing_field,
    }


def cross_source_table(
    series_by_source: dict[str, Any], concept: str | None = None, week_keys: set[Any] | None = None
) -> list[dict[str, Any]]:
    """跨 series 对照（宽表）：每概念一张宽表。

    返回 list of {concept, display, header, rows, note, has_data, all_zero,
    missing_sources}。每概念只一块；同概念的 series 两两差异写进 note
    （不是再产 C(N,2) 张表）。deprecated / META_FIELDS / DEPRECATED 不参与。
    concept 过滤：concept 与 display 都接受。
    """
    out = []
    all_concepts: dict[str, dict[str, Any]] = metrics_mod.CONCEPTS
    for cname, c in all_concepts.items():
        non_dep = [(k, s) for k, s in c["series"].items() if not s.get("deprecated")]
        if len(non_dep) < 2:
            continue  # 单 series 概念不生成跨 series 对照
        if concept and concept not in (cname, c["display"]):
            continue
        wide = wide_rows_for_concept(series_by_source, cname, week_keys)
        note = _concept_note(c, non_dep)
        out.append(
            {
                "concept": cname,
                "display": c["display"],
                "header": wide["header"],
                "rows": wide["rows"],
                "note": note,
                "has_data": wide["has_data"],
                "all_zero": wide.get("all_zero", False) or wide.get("all_rows_filtered", False),
                "missing_sources": wide.get("missing_sources", []),
                "missing_fields": wide.get("missing_fields", []),
                "no_intersect": wide.get("no_intersect", False),
            }
        )
    return out


def _concept_note(c: dict[str, Any], non_dep: list[Any]) -> str:
    """概念级「不可直接比」说明（窗口/分母差异逐对列出）。"""
    parts = []
    for a, b in combinations([k for k, _ in non_dep], 2):
        sa, sb = c["series"][a], c["series"][b]
        win_diff = sa["window"] != sb["window"]
        den_diff = sa["denominator"] != sb["denominator"]
        if not (win_diff or den_diff):
            continue
        bits = []
        if win_diff:
            bits.append(f"窗口 {sa['window']} vs {sb['window']}")
        if den_diff:
            bits.append(f"分母「{sa['denominator']}」 vs 「{sb['denominator']}」")
        parts.append(f"`{a}` vs `{b}`：{'；'.join(bits)}")
    return f"⚠ 同一概念「{c['display']}」多 series 不可直接比：{'；'.join(parts)}"


_CHANNEL_HDR = [
    "文章",
    "发表日",
    "阅读人数",
    "完读率",
    "平均时长(秒)",
    "推荐%",
    "公众号消息%",
    "搜一搜%",
]


def channel_detail_lines(det: list[Any]) -> list[Any]:
    """单篇渠道构成节（纯）。

    `det` 是壳备好的「本周已按 (appmsgid, itemidx) 去重取最新」的快照三元组。
    同一篇多天快照只取最新那条，否则客户看到同一标题两行（不同渠道占比），
    会问「这为什么有两条」。
    """
    if not det:
        return []
    rows2 = [r for r in (channel_row(title, m) for _sd, title, m in det) if r is not None]
    out = [
        "",
        "## 单篇渠道构成（完读率 + 渠道来源，30 天窗口口径）",
        md_table(_CHANNEL_HDR, rows2),
    ]
    if rows2:
        out.append(
            "（渠道占比分母=「全部」渠道阅读人数；同一用户多渠道重复计数，占比之和可 >100%）"
        )
    else:
        out.append("（本周无 read_uv > 0 的单篇快照。）")
    return out


def guidance_lines(adv_rows: list[Any]) -> tuple[list[Any], dict[str, Any]]:
    """「本周该做什么」判断层 + 工具边界（纯）：advice 行数据进，周报小节出。

    R240：只给数字不给判断 = 把客户自己的活儿还给他。判断用 mpdt 自己抓的
    channel_breakdown（推荐渠道绝对阅读人数），不需要客户导出任何东西。
    """
    out: list[Any] = []
    try:
        adv = advice_mod.advise(adv_rows)
        out.append(advice_mod.render(adv))
        info = {
            "verdict": adv.get("verdict"),
            "headline": adv.get("headline"),
            "n_actions": len(adv.get("actions") or []),
            "evidence": adv.get("evidence", {}),
        }
        # R67：把「工具到哪一步为止 / 剩下归谁」写进周报本身。
        # 实测：mpdt 14 天 133 个唯一克隆者，但 ¥99/¥499 三档 0 成交 ——
        # 终端里从没出现过「人工版」，用完工具的人根本不知道有人能接着做。
        # 只在有可执行结论时出现（样本不足时出现就是骗人）。
        try:
            ns = next_step_mod.render_next_step(adv)
            if ns:
                out.append(ns)
                info["next_step"] = True
        except Exception:
            info["next_step"] = False
    except Exception as exc:
        info = {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        out.append("> ⚠ 判断层未能生成：{}\n".format(info["error"]))
    return out, info


def build_report(
    iso_week: str, week_begin: str, week_end: str, data: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """周报正文 + 摘要 —— 纯函数：不进数据库、不写文件、不打印。

    `data` 是壳查出来的原始行（见 report.collect），这里做完所有计算与排版。
    """
    arts = week_snapshots(data["detail_rows"], week_begin, week_end)
    dc_arts = week_snapshots(data["dc_rows"], week_begin, week_end)
    top = completion_top(arts, dc_arts)

    lines = [
        f"# 公众号数据周报 {iso_week}（{week_begin} ~ {week_end}）",
        "",
        calibration_note(),
        "",
    ]
    lines.append(
        f"**本周发表的文章**：**{top['articles']}** 篇（口径 = 文章发表日落在 "
        f"{week_begin} ~ {week_end}，不是抓取日；单篇渠道页口径，含推荐/公众号消息/搜一搜"
        f"渠道构成；原始快照 {len(arts)} 行，每日快照已按文章去重取最新）"
        f"；datacube 补充 {top['dc_articles']} 篇（文章级 7 天窗口）"
    )
    lines.append("")
    if top["fake_perfect"]:
        lines.append(
            f"> 已排除 {top['fake_perfect']} 篇「分母为 0 的伪 100% 完读率」"
            f"（微信在无人读完时返回 100%，非内容表现好）。"
        )
        lines.append("")
    if top["n"]:
        n = top["n"]
        suffix = "" if n == 10 else f"（共 {n} 篇）"
        lines.append(f"## 完读率 Top{n}{suffix}（单篇渠道页口径；ad30=30天窗口, dc7=发表后7天）")
        lines.append(
            md_table(
                ["文章", "完读率", "阅读uv", "推荐占比", "均阅读时长", "阅读率", "口径"],
                top["rows"],
            )
        )
    else:
        lines.append("本周无 datacube 快照数据（fetch 未覆盖该周，或均为 T+1 未出）。")

    guide, advice_info = guidance_lines(data["advice_rows"])
    lines.extend(guide)

    # 跨 series 对照（宽表）：折叠无数据 / 全 0 概念
    blocks = cross_source_table(data["series_by_source"], week_keys=data["week_keys"])
    cross_rows_total = 0
    has_data_concepts = [blk for blk in blocks if blk["has_data"] and not blk["all_zero"]]
    folded_concepts = [blk for blk in blocks if (not blk["has_data"]) or blk["all_zero"]]
    if blocks:
        lines.append("")
        lines.append("## 跨 series 对照（每概念一张宽表；同概念多 series 不可直接比）")
        for blk in has_data_concepts:
            lines.append("")
            lines.append("### 概念「{}」".format(blk["display"]))
            lines.append(md_table(blk["header"], blk["rows"]))
            cross_rows_total += len(blk["rows"])
            if blk["note"]:
                lines.append(blk["note"])
        lines.extend(folded_concept_lines(folded_concepts))
    else:
        lines.append("")
        lines.append("## 跨 series 对照")
        lines.append("（本周 CONCEPTS 中无多 series 概念，无需对照。）")

    lines.extend(channel_detail_lines(data["detail_in_week"]))

    # rows 语义 = 本周发表的文章数（两源并集，按标题去重）
    week_articles = {ti for (_sd, ti, _m) in arts} | {ti for (_sd, ti, _m) in dc_arts}
    return "\n".join(lines) + "\n", {
        "rows": len(week_articles),
        "advice": advice_info,
        "cross_rows": cross_rows_total,
        "concepts_compared": [blk["concept"] for blk in has_data_concepts],
        "concepts_empty": [blk["concept"] for blk in folded_concepts],
    }


def build_html(iso_week: str, md: str) -> str:
    """周报正文 → 独立 HTML 文档（纯）。"""
    return _HTML_TEMPLATE.format(title=f"mpdt 周报 {iso_week}", body=md_to_html(md))


def report_intro(week: str) -> str:
    """默认邮件开场白（纯）。"""
    return (
        f"您好，\n\n这是您 {week} 的公众号数据周报（数据口径：文章发表日落在该周内）。\n"
        "回复本邮件即可把问题直接发给我。\n\n"
    )


def compose_report_email(
    sender: str,
    recipient: str,
    week: str,
    md_text: str,
    *,
    html_text: str = "",
    subject: str = "",
    body_md: str = "",
) -> EmailMessage:
    """组装周报邮件（纯）：给定全部内容，返回 EmailMessage，不碰网络。

    发信是壳的事（push.send_report）。这里只要内容，所以可以拿一封假周报
    直接断言标题/正文/alternative 结构。
    """
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject or (f"公众号数据周报 {week}")
    intro = body_md or report_intro(week)
    msg.set_content(intro + "\n" + md_text)
    if html_text:
        msg.add_alternative(html_text, subtype="html")
    return msg


def human_bytes(n: int) -> str:
    """字节数 → 人读（KB/MB）。数据库体积这种数字，人只看量级。"""
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.2f} MB"


def status_lines(
    db_path: str,
    db_exists: bool,
    size_h: str,
    rows_by_source: dict[str, Any],
    latest: str | None,
    db_bytes: int,
) -> list[str]:
    """`mpdt status` 的人读正文（纯）：不查库，只把已知事实排成行。"""
    lines = [f"数据库：{db_path}" + ("" if db_exists else "（尚不存在，首次 fetch 时创建）")]
    if not db_exists:
        return lines
    for src, n in rows_by_source.items():
        lines.append(f"  {src:<18} {n} 行")
    lines.append(f"  最新快照日：{latest or '（空库）'}")
    lines.append(f"  库体积：{size_h}（{db_bytes} 字节）")
    return lines


def status_summary(db_path: str, total_rows: int, size_h: str) -> str:
    return f"库状态：{db_path}（{total_rows} 行，{size_h}）"


def export_message(
    fmt: str, out: str, rows: int, source: str, with_meta: bool
) -> tuple[str, dict[str, Any]]:
    """导出一行的结论文案 + 元数据（纯）：csv 与 json 的措辞不同，别在壳里 if。"""
    if fmt == "json":
        msg = f"JSON 导出：{out}（{rows} 行，source={source}）"
        meta = {"out": out, "rows": rows, "source": source, "format": "json"}
        if with_meta:
            msg += "（口径元数据：内嵌 metrics_meta）"
            meta["metrics_meta"] = "embedded"
        return msg, meta
    msg = f"CSV 导出：{out}（{rows} 行，source={source}）"
    meta = {"out": out, "rows": rows, "source": source, "format": "csv"}
    if with_meta:
        msg += f"（口径元数据：{out}.metrics.csv）"
        meta["metrics_meta"] = out + ".metrics.csv"
    return msg, meta


def push_success_message(
    recipient: str, week: str, nbytes: int, verify: dict[str, Any], verify_requested: bool
) -> str:
    """推送成功的一行结论（纯）。

    R68：SMTP 接受 ≠ 送达。没回查就明说"未验证"，别让人读成"客户收到了"。
    """
    msg = f"已推送：{recipient} · {week}（{nbytes} 字节，HTML 内联）"
    if verify.get("delivered") is True:
        msg += "；已回查确认送达"
    elif verify.get("delivered") is None and not verify_requested:
        msg += "（SMTP 已接受，未验证收件方收到）"
    return msg


def push_undelivered_message(recipient: str, error: str | None) -> str:
    return (
        f"⚠ SMTP 接受但**未验证送达**：{recipient}（{error}）。请人工确认对方收件箱，勿当成已送达。"
    )
