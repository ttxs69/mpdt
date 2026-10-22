"""周报生成（FR5）：Markdown + HTML；文首口径说明（R4）——由 metrics.py CONCEPTS 生成。

跨 series 对照：每个 ≥2 series 概念出一张**宽表**（列：文章 / appmsgid_itemidx /
各 series 值 / 差值说明列），不再生成 C(N,2) 张两两对照表。
- 差值说明列：仅当 series 含"比例"单位时显示（即只对 ratio 类型概念显示），
  内容是「系列两两差"的文字说明」（与旧"差值(百分点)"列等价，但不重复 C(N,2) 次）。
- 无数据的概念折叠成一行汇总（不再每概念占一节）。
"""

import datetime as dt
import html as html_mod
import json
import os
from itertools import combinations

from . import advice as advice_mod
from . import metrics as metrics_mod
from . import next_step as next_step_mod
from .metrics import calibration_note

_MD_INLINE_RE = None  # 延迟编译（re 非必需依赖面）

# 锚点 id 前缀：h2 → sec-0/sec-1/…（目录导航用；与 _md_to_html 的两遍处理配套）
_ANCHOR_PREFIX = "sec-"


def _extract_toc(md: str) -> list:
    """从 md 提取 h2 目录（标题, anchor_id）。供 _md_to_html 生成目录导航。"""
    toc = []
    i = 0
    for line in md.split("\n"):
        if line.startswith("## "):
            toc.append((line[3:].strip(), f"{_ANCHOR_PREFIX}{i}"))
            i += 1
    return toc


def _inline_html(text: str) -> str:
    """行内 markdown → HTML：先转义，再 **加粗**、`行内码`。

    本函数只服务本模块自己生成的 md 子集（无嵌套、无链接），不追求通用。
    """
    import re

    text = html_mod.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def _md_table_html(header_line: str, body_lines: list) -> str:
    """| a | b | 形式的表格行 → <table>（首行表头；第二行是 --- 分隔线，调用方已剔除）。"""

    def cells(line):
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        return parts

    h = cells(header_line)
    out = [
        "<table>",
        "<thead><tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in h) + "</tr></thead>",
    ]
    out.append("<tbody>")
    for line in body_lines:
        cs = cells(line)
        # 列数对齐表头（多余截断、不足补空，防错位）
        cs = (cs + [""] * len(h))[: len(h)]
        out.append("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in cs) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _md_to_html(md: str) -> str:
    """本报告 md 子集 → HTML 片段（标题/表格/引用/段落）+ 文首目录导航（锚点）。

    只处理 generate() 会产出的结构：#/##/### 标题、| 表格（含 --- 分隔行）、
    > 引用块、普通段落行。不支持列表/链接/嵌套——超出子集的行按段落渲染。
    h2 标题顺次编 id=sec-N，目录由 _extract_toc 推导（两遍一致）。
    """
    lines = md.split("\n")
    out = []
    toc = _extract_toc(md)
    if toc:
        nav = [
            '<nav class="toc">',
            "<b>目录</b>",
            "".join(f'<a href="#{a}">{_inline_html(t)}</a>' for t, a in toc),
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
        if (
            s.startswith("|")
            and i + 1 < n
            and set(lines[i + 1].strip()) <= {"|", "-", ":", " "}
            and "-" in lines[i + 1]
        ):
            # 表格块：首行表头 + 分隔行 + 数据行（连续 | 开头）
            j = i + 2
            body = []
            while j < n and lines[j].strip().startswith("|"):
                body.append(lines[j])
                j += 1
            out.append(_md_table_html(line, body))
            i = j
            continue
        if s.startswith("### "):
            out.append(f"<h3>{_inline_html(s[4:])}</h3>")
        elif s.startswith("## "):
            out.append(f'<h2 id="{_ANCHOR_PREFIX}{h2_i}">{_inline_html(s[3:])}</h2>')
            h2_i += 1
        elif s.startswith("# "):
            out.append(f"<h1>{_inline_html(s[2:])}</h1>")
        elif s.startswith(">"):
            # 连续 > 行合并为一个 blockquote
            quoted = []
            while i < n and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(
                "<blockquote>" + "<br>".join(_inline_html(q) for q in quoted) + "</blockquote>"
            )
            continue
        else:
            out.append(f"<p>{_inline_html(s)}</p>")
        i += 1
    return "\n".join(out)


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


def _week_bounds(iso_week: str):
    year, week = iso_week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def _md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _latest_by_key(con, source):
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


def _fmt_value(v, is_ratio):
    """把数值格式化为人读串（ratio 概念 ×100%，其它按原值）。"""
    if not isinstance(v, (int, float)):
        return "-"
    if is_ratio:
        return "%.1f%%" % (100 * v)
    return str(int(v)) if float(v).is_integer() else (f"{v:.1f}")


def _week_article_keys(con, b, e):
    """本周**发表**的文章 key 集合 {(appmsgid, itemidx)}。

    R238：宽表（跨 series 对照）原来取全库最新快照 ⇒ 混进两周前的文章。
    现在按每篇文章自己的发表日过滤。
    发表日来源：ad30 用 publish_date；dc7 用 refdate(20261007 或 2026-10-07)；pl 用 publish_date。
    """

    def _pd(m):
        v = m.get("publish_date")
        if v and len(str(v)) >= 10:
            return str(v)[:10]
        r = m.get("refdate")
        if r:
            rs = str(r).strip()
            if len(rs) == 8 and rs.isdigit():
                return f"{rs[:4]}-{rs[4:6]}-{rs[6:8]}"
            if len(rs) >= 10:
                return rs[:10]
        return None

    keys = set()
    for _src, am, ix, sd, m in con.execute(
        "SELECT source, appmsgid, itemidx, stat_date, metrics FROM snapshots"
    ).fetchall():
        dm = json.loads(m)
        pd = _pd(dm)
        if pd:
            hit = b <= pd <= e
        else:
            # 无发表日字段的老数据：按抓取日兜底，不丢数据
            hit = b <= sd <= e
        if hit:
            keys.add((str(am), int(ix)))
    return keys


def _wide_rows_for_concept(con, cname, week_keys=None):
    """生成某概念一张宽表 → {header, rows, has_data, all_zero, missing_sources}。

    列 = [文章, appmsgid_itemidx, s1 值, s2 值, …, 差值说明列?]
    行 = 仅包含 (appmsgid, itemidx) 命中概念下**所有 series 源**都有快照的交集；
    且每个 series 字段都有数值。空交集 → has_data=False，周报层折叠。

    all_zero：宽表全部数值为 0（无信号），调用方应折叠。
    missing_sources：哪些源没覆盖到此概念（给读者诊断"为什么没数据"）。
    """
    c = metrics_mod.CONCEPTS[cname]
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
    by_source = {s["source"]: _latest_by_key(con, s["source"]) for _, s in series}
    keys_union = set()
    for src_map in by_source.values():
        keys_union.update(src_map)
    if week_keys is not None:
        keys_union = {k for k in keys_union if k in week_keys}
    rows = []
    any_nonzero = False
    for key in sorted(keys_union):
        _sd_a, ti_a, _mm_a = by_source[series[0][1]["source"]].get(key, (None, "", {}))
        all_ok = True
        cells = []
        vals = []
        for _skey, s in series:
            mm = by_source[s["source"]].get(key, (None, "", {}))[2]
            v = mm.get(s["field"])
            if not isinstance(v, (int, float)):
                all_ok = False
                break
            vals.append(v)
            cells.append(_fmt_value(v, is_ratio))
            if v != 0:
                any_nonzero = True
        if not all_ok:
            continue
        # 比例概念补差值说明列（两两差）
        diffs = []
        if is_ratio and len(series) >= 2:
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
        title = (ti_a or "")[:36]
        row = [title, "%s_%d" % key, *cells]
        if is_ratio and len(series) >= 2:
            row.append("；".join(f"{a}−{b}={100 * d:+.1f}pt" for a, b, d, _ in diffs))
        # 「全 0」行折叠（噪音过滤）：宽表只保留至少一个 series 非零的行
        if any(v != 0 for v in vals):
            rows.append(row)

    header = ["文章", "appmsgid_itemidx"] + [
        "%s(%s·%d天)" % (c["display"], skey, s["window_days"]) for skey, s in series
    ]
    if is_ratio and len(series) >= 2:
        header.append("差值(百分点·两两差)")
    # 缺失源/字段诊断：哪些源完全没此概念的快照，或哪个 field 在该源任何快照都不存在
    missing_src = [sd["source"] for _, sd in series if not by_source[sd["source"]]]
    missing_field = []
    if rows == [] and not missing_src:
        for _skey, s in series:
            # _latest_by_key.values() 是 (sd, ti, mm) 三元组；mm 才是指标 dict
            if not any(s["field"] in row[2] for row in by_source[s["source"]].values()):
                missing_field.append("{}(`{}`)".format(s["source"], s["field"]))
    # 单独追踪：all rows in 交集 all-zero（被「行级」过滤掉 → has_data 假但非"无源/无字段"）
    # 区分两种情况：① union 非空但没有任何行通过 all_ok（典型：两源 appmsgid 不重叠）
    # ② 全部行因 all-zero 被过滤掉
    has_any_intersect = False
    for key in keys_union:
        all_ok = True
        for _skey, s in series:
            v = by_source[s["source"]].get(key, (None, "", {}))[2].get(s["field"])
            if not isinstance(v, (int, float)):
                all_ok = False
                break
        if all_ok:
            has_any_intersect = True
            break
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


def cross_source_table(con, concept: str | None = None, week_keys=None) -> list:
    """跨 series 对照（宽表）：每概念一张宽表。

    返回 list of {concept, display, header, rows, note, has_data, all_zero,
    missing_sources}。每概念只一块；auto_pairs 用于生成 note 中的「不可直接比」
    明细（不是再产 C(N,2) 张表）。deprecated / META_FIELDS / DEPRECATED 不参与。
    concept 过滤：concept 与 display 都接受。
    """
    out = []
    for cname, c in metrics_mod.CONCEPTS.items():
        non_dep = [(k, s) for k, s in c["series"].items() if not s.get("deprecated")]
        if len(non_dep) < 2:
            continue  # 单 series 概念不生成跨 series 对照
        if concept and concept not in (cname, c["display"]):
            continue
        wide = _wide_rows_for_concept(con, cname, week_keys)
        # 概念级不可比说明（用 auto_pairs 的同概念条目）
        parts = []
        for a, b in combinations([k for k, _ in non_dep], 2):
            sa, sb = c["series"][a], c["series"][b]
            win_diff = sa["window"] != sb["window"]
            den_diff = sa["denominator"] != sb["denominator"]
            if win_diff or den_diff:
                bits = []
                if win_diff:
                    bits.append("窗口 {} vs {}".format(sa["window"], sb["window"]))
                if den_diff:
                    bits.append("分母「{}」 vs 「{}」".format(sa["denominator"], sb["denominator"]))
                parts.append("`{}` vs `{}`：{}".format(a, b, "；".join(bits)))
        note = "⚠ 同一概念「{}」多 series 不可直接比：{}".format(c["display"], "；".join(parts))
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


def generate(con, iso_week: str, out_dir: str) -> dict:
    monday, sunday = _week_bounds(iso_week)
    b, e = monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    # R238：周的语义 = **本周发表的文章**，不是「本周抓到的快照」。
    # 旧实现按 stat_date（抓取日）筛：补抓历史会把上周文章塞进本周周报。
    # 实测 W43(10-19~10-25)：datacube 命中 5 行里，发表日在窗口内的 **0 行**；
    # article_detail 48 行里发表日在窗口内的只有 13 行。
    # 客户拿到这种周报第一反应是「这都是上周的文章吧」⇒ 判定口径错，不是数据少。
    # 修法：SQL 拿窗口内所有快照，Python 侧按文章自己的发表日过滤。
    rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
        "ORDER BY stat_date"
    ).fetchall()
    # R237：周报主表改用 article_detail 源，不用 datacube_article。
    # 实测本号：datacube_article 只有 17 行、覆盖 3 天，article_detail 有 87 行、每天 11-20 篇。
    # datacube 是「文章级 7 天窗口」，采集频率低（T+1 且不追历史）⇒ 周报只剩 4-5 篇，
    # 客户一看就问「我一周发 20 篇，怎么只统计了 4 篇」。
    # article_detail 有 read_uv + 渠道构成（推荐/公众号消息/搜一搜）—— 客户真正要看的在这。
    detail_rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='article_detail' "
        "ORDER BY stat_date"
    ).fetchall()

    def _pub_date(m):
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

    def _with_sd(sd, m):
        d = json.loads(m)
        d["_stat_date"] = sd
        return d

    def _in_week(m):
        pd = _pub_date(m)
        if pd is None:
            # 没有发表日字段的快照（旧数据 / 部分来源）：退回按抓取日 stat_date 判断，
            # 不能因为缺字段就把数据整段丢掉。
            sd = m.get("_stat_date")
            return sd is not None and b <= sd <= e
        return b <= pd <= e

    # 只留「发表日在本周」的；同文章多日快照按文章去重取最新
    arts = [(sd, ti, _with_sd(sd, m)) for sd, ti, m in detail_rows if _in_week(_with_sd(sd, m))]
    dc_arts = [(sd, ti, _with_sd(sd, m)) for sd, ti, m in rows if _in_week(_with_sd(sd, m))]
    # 完读率 Top10：优先用 article_detail 的 finished_read_pv_ratio（30 天窗口，有 read_uv 分母），
    # 回退到 datacube 的 finish_read_rate（7 天窗口）。
    # 去重：article_detail 是「每日快照」，同一篇文章会在多天各一行
    # （实测「rea 屠榜 GitHub」在 10-19/10-20/10-21 各一行）。
    # 客户看到同标题重复出现会立刻质疑。规则：同一 appmsgid+itemidx 只取**最新 stat_date** 那一行
    # （最新 = 数据最全），并把取了几行、覆盖几天写进口径行。
    latest_by_art = {}
    for sd, ti, m in arts:
        key = ti
        prev = latest_by_art.get(key)
        if prev is None or sd > prev[0]:
            latest_by_art[key] = (sd, m)
    by_key = {}
    for ti, (sd, m) in latest_by_art.items():
        fr = m.get("finished_read_pv_ratio")
        if fr is not None:
            by_key[(ti, sd)] = (fr, m, "ad30")
    dc_latest = {}
    dc_by_art = {}
    for sd, ti, m in dc_arts:
        if ti not in dc_latest or sd > dc_latest[ti][0]:
            dc_latest[ti] = (sd, m)
        dc_by_art[ti] = m
    # 同一篇文章若 ad30 已有，则不再补 dc7（否则 Top 表里同一篇文章出现两行，
    # 客户会问「这篇为什么有两条」）。dc7 只用于 ad30 缺失的文章。
    have_arts = {ti for (ti, _sd) in by_key}
    for ti, (sd, m) in dc_latest.items():
        if ti in have_arts:
            continue
        if m.get("finish_read_rate"):
            by_key[(ti, sd)] = (m["finish_read_rate"], m, "dc7")

    # R237：剔除「分母为 0 的伪 100%」。
    # 微信在 total_read_finish=0（没人读完）时把 finish_read_rate 返回 1（即 100%），
    # 而不是空值。直接排序会让这些行冲到 Top1 —— 实测本号 17 行里 2 行（11.8%）是这种。
    # 客户看到「完读率 100%」会立刻质疑，而这是分母为 0，不是内容好。
    # 判据：finish_read_rate >= 1.0 且 total_read_finish == 0 ⇒ 判为无意义值，排除出排名。
    def _fake_perfect(entry):
        fr, m, src = entry
        try:
            if src == "dc7":
                # datacube：finish_read_rate>=1 且 total_read_finish==0 ⇒ 分母为 0 的伪 100%
                return float(fr) >= 1.0 and int(m.get("total_read_finish") or 0) == 0
            # article_detail：完读率 1.0 但 read_uv 很小（<5 人）时，1 人读完 = 100%，无统计意义
            return float(fr) >= 1.0 and int(m.get("read_uv") or 0) < 5
        except (TypeError, ValueError):
            return False

    fake_perfect = {k for k, v in by_key.items() if _fake_perfect(v)}
    if fake_perfect:
        by_key = {k: v for k, v in by_key.items() if k not in fake_perfect}
    top = sorted(by_key.items(), key=lambda kv: -float(kv[1][0]))[:10]

    def _top_row(kv):
        ti, _sd = kv[0]
        fr, m, src = kv[1]
        uv = m.get("read_uv")
        ch = m.get("channel_breakdown") or []
        rec = next((c for c in ch if c.get("scene_label") == "推荐"), None)
        rec_pct = rec.get("read_pct") if isinstance(rec, dict) else None
        avg = m.get("avg_article_read_time")
        # 阅读率（阅读/送达）只在 datacube 源有；该文章有 dc7 行时回填，
        # 否则留 '-'（不许用别的口径冒充）。
        rr = None
        dmrow = dc_by_art.get(ti)
        if dmrow:
            rr = dmrow.get("read_rate")
        return [
            ti[:36],
            f"{float(fr):.1%}",
            (uv if uv is not None else "-"),
            (f"{rec_pct:.0f}%" if isinstance(rec_pct, (int, float)) else "-"),
            (f"{avg:.0f}s" if isinstance(avg, (int, float)) else "-"),
            (f"{rr:.1%}" if isinstance(rr, (int, float)) else "-"),
            src,
        ]

    top_rows = [_top_row(kv) for kv in top]
    lines = [f"# 公众号数据周报 {iso_week}（{b} ~ {e}）", "", calibration_note(), ""]
    lines.append(
        f"**本周发表的文章**：**{len(latest_by_art)}** 篇（口径 = 文章发表日落在 {b} ~ {e}，"
        f"不是抓取日；单篇渠道页口径，含推荐/公众号消息/搜一搜渠道构成；"
        f"原始快照 {len(arts)} 行，每日快照已按文章去重取最新）"
        f"；datacube 补充 {len(dc_latest)} 篇（文章级 7 天窗口）"
    )
    lines.append("")
    if fake_perfect:
        lines.append(
            f"> 已排除 {len(fake_perfect)} 篇「分母为 0 的伪 100% 完读率」"
            f"（微信在无人读完时返回 100%，非内容表现好）。"
        )
        lines.append("")
    if top:
        n = min(10, len(top))
        suffix = "" if n == 10 else f"（共 {len(top)} 篇）"
        lines.append(
            "## 完读率 Top%d%s（单篇渠道页口径；ad30=30天窗口, dc7=发表后7天）" % (n, suffix)
        )
        lines.append(
            _md_table(
                ["文章", "完读率", "阅读uv", "推荐占比", "均阅读时长", "阅读率", "口径"], top_rows
            )
        )
    else:
        lines.append("本周无 datacube 快照数据（fetch 未覆盖该周，或均为 T+1 未出）。")

    # ---- 本周该做什么（判断层）----
    # R240：只给数字不给判断 = 把客户自己的活儿还给他。
    # 判断层用 mpdt 自己抓的 channel_breakdown（推荐渠道绝对阅读人数），
    # 不需要客户导出任何东西就能给出第一条结论。
    try:
        _rows = advice_mod.load_rows(con)
        _adv = advice_mod.advise(_rows)
        lines.append(advice_mod.render(_adv))
        advice_info = {
            "verdict": _adv.get("verdict"),
            "headline": _adv.get("headline"),
            "n_actions": len(_adv.get("actions") or []),
            "evidence": _adv.get("evidence", {}),
        }
        # R67：把「工具到哪一步为止 / 剩下归谁」写进周报本身。
        # 实测：mpdt 14 天 133 个唯一克隆者，但 ¥99/¥499 三档 0 成交 ——
        # 终端里从没出现过「人工版」，用完工具的人根本不知道有人能接着做。
        # 只在有可执行结论时出现（样本不足时出现就是骗人）。
        try:
            _ns = next_step_mod.render_next_step(_adv)
            if _ns:
                lines.append(_ns)
                advice_info["next_step"] = True
        except Exception:
            advice_info["next_step"] = False
    except Exception as _e:
        advice_info = {"verdict": "ERROR", "error": f"{type(_e).__name__}: {_e}"}
        lines.append("> ⚠ 判断层未能生成：{}\n".format(advice_info["error"]))

    # 跨 series 对照（宽表）：折叠无数据 / 全 0 概念
    # R238：宽表也按周筛（只留本周发表的文章）
    week_keys = _week_article_keys(con, b, e)
    blocks = cross_source_table(con, week_keys=week_keys)
    cross_rows_total = 0
    has_data_concepts = [b for b in blocks if b["has_data"] and not b["all_zero"]]
    folded_concepts = [b for b in blocks if (not b["has_data"]) or b["all_zero"]]
    if blocks:
        lines.append("")
        lines.append("## 跨 series 对照（每概念一张宽表；同概念多 series 不可直接比）")
        for blk in has_data_concepts:
            lines.append("")
            lines.append("### 概念「{}」".format(blk["display"]))
            lines.append(_md_table(blk["header"], blk["rows"]))
            cross_rows_total += len(blk["rows"])
            if blk["note"]:
                lines.append(blk["note"])
        if folded_concepts:
            # 按"为什么折叠"分组
            missing_src = [b for b in folded_concepts if not b["has_data"] and b["missing_sources"]]
            missing_field = [
                b
                for b in folded_concepts
                if not b["has_data"] and not b["missing_sources"] and b["missing_fields"]
            ]
            no_intersect = [b for b in folded_concepts if b["no_intersect"]]
            all_zero_no_rows = [
                b
                for b in folded_concepts
                if b["all_zero"]
                and not b["has_data"]
                and not b["missing_sources"]
                and not b["missing_fields"]
            ]
            zero = [b for b in folded_concepts if b["has_data"] and b["all_zero"]]
            if missing_src:
                by_missing = {}
                # R238：循环变量不能叫 b —— b 是周界（week begin），for 循环会覆盖它。
                # 旧代码用 b 且此后没再读 b，所以一直没炸；周筛延伸到本节之后才暴露。
                for blk in missing_src:
                    key = "+".join(sorted(blk["missing_sources"]))
                    by_missing.setdefault(key, []).append(blk["display"])
                parts = [
                    "{}（缺 {} 快照）".format("、".join(names), miss)
                    for miss, names in by_missing.items()
                ]
                lines.append("")
                lines.append("> **本周无可对照数据的概念**：{}。".format("；".join(parts)))
            if missing_field:
                by_mf = {}
                for blk in missing_field:
                    key = "+".join(sorted(blk["missing_fields"]))
                    by_mf.setdefault(key, []).append(blk["display"])
                parts = [
                    "{}（缺字段 {}）".format("、".join(names), mf) for mf, names in by_mf.items()
                ]
                lines.append("> **本周无可对照数据的概念**：{}。".format("；".join(parts)))
            if no_intersect:
                lines.append(
                    "> **本周无可对照数据的概念（快照 appmsgid 无交集）**：{}。".format(
                        "、".join(b["display"] for b in no_intersect)
                    )
                )
            if all_zero_no_rows:
                lines.append(
                    "> **本周宽表无信号（全 0 行）**：{}。".format(
                        "、".join(b["display"] for b in all_zero_no_rows)
                    )
                )
            if zero:
                lines.append(
                    "> **本周宽表无信号（全 0 概念）**：{}。".format(
                        "、".join(b["display"] for b in zero)
                    )
                )
    else:
        lines.append("")
        lines.append("## 跨 series 对照")
        lines.append("（本周 CONCEPTS 中无多 series 概念，无需对照。）")

    # 单篇渠道构成（FR3②，30 天窗口口径）
    # R238：同样按发表日筛周 —— 之前这节直接取全库最新 15 行，
    # 会把上周（甚至更早）的文章混进本周周报，客户一眼看出错。
    # 按 (appmsgid, itemidx) 去重：同一篇文章多天快照只取最新那条。
    # 否则客户看到同一标题两行（不同渠道占比），会问「这为什么有两条」。
    det_map = {}
    for sd, ti, m, am, ix in con.execute(
        "SELECT stat_date, title, metrics, appmsgid, itemidx FROM snapshots "
        "WHERE source='article_detail' ORDER BY stat_date DESC, appmsgid DESC"
    ).fetchall():
        dm = _with_sd(sd, m)
        if not _in_week(dm):
            continue
        k = (str(am), int(ix))
        if k not in det_map:  # rows 已按 stat_date DESC 排序 ⇒ 首个即最新
            det_map[k] = (sd, ti, dm)
    det = list(det_map.values())[:15]
    if det:
        lines.append("")
        lines.append("## 单篇渠道构成（完读率 + 渠道来源，30 天窗口口径）")
        hdr = [
            "文章",
            "发表日",
            "阅读人数",
            "完读率",
            "平均时长(秒)",
            "推荐%",
            "公众号消息%",
            "搜一搜%",
        ]

        def _pct(m, scene):
            for r in m.get("channel_breakdown") or []:
                if r.get("scene") == scene:
                    return r.get("read_pct")
            return None

        rows2 = []
        # det 的 metrics 已是 dict（R238 周筛时解析过），不要再 json.loads
        for sd, ti, m in det:
            ruv = m.get("read_uv")
            # 跳过 read_uv=0 的"无信号"行（与折叠精神一致）
            if not isinstance(ruv, (int, float)) or ruv == 0:
                continue
            fr = m.get("finished_read_pv_ratio")
            rows2.append(
                [
                    (ti or "")[:36],
                    m.get("publish_date", ""),
                    ruv,
                    ("%.1f%%" % (100 * fr)) if isinstance(fr, (int, float)) else "-",
                    m.get("avg_article_read_time", ""),
                    _pct(m, 6) if _pct(m, 6) is not None else "-",
                    _pct(m, 0) if _pct(m, 0) is not None else "-",
                    _pct(m, 7) if _pct(m, 7) is not None else "-",
                ]
            )
        lines.append(_md_table(hdr, rows2))
        if rows2:
            lines.append(
                "（渠道占比分母=「全部」渠道阅读人数；同一用户多渠道重复计数，占比之和可 >100%）"
            )
        else:
            lines.append("（本周无 read_uv > 0 的单篇快照。）")
    md = "\n".join(lines) + "\n"
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"weekly-{iso_week}.md")
    open(md_path, "w").write(md)
    html_path = os.path.join(out_dir, f"weekly-{iso_week}.html")
    open(html_path, "w").write(
        _HTML_TEMPLATE.format(title=f"mpdt 周报 {iso_week}", body=_md_to_html(md))
    )
    assert open(md_path).read() == md and "口径说明" in open(md_path).read()
    # 模板化 HTML 读回断言（P3：机制生效而非碰巧）。注意空库周报无表格——
    # <table> 存在性由带数据的 TestGenerate/TestHtmlTemplate 覆盖，此处只断言不变量。
    h = open(html_path).read()
    assert f"<title>mpdt 周报 {iso_week}</title>" in h and "<pre>" not in h and "口径说明" in h, (
        "HTML 模板化断言失败"
    )
    # rows 语义 = 本周发表的文章数（article_detail 与 datacube 两源并集，按标题去重）
    week_articles = set(ti for (_sd, ti, _m) in arts) | set(ti for (_sd, ti, _m) in dc_arts)
    return {
        "md": md_path,
        "html": html_path,
        "rows": len(week_articles),
        "advice": advice_info,
        "cross_rows": cross_rows_total,
        "concepts_compared": [b["concept"] for b in has_data_concepts],
        "concepts_empty": [b["concept"] for b in folded_concepts],
    }
