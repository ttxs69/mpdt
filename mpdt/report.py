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

from . import metrics as metrics_mod
from .metrics import calibration_note, auto_pairs


_MD_INLINE_RE = None  # 延迟编译（re 非必需依赖面）


def _inline_html(text: str) -> str:
    """行内 markdown → HTML：先转义，再 **加粗**、`行内码`。

    本函数只服务本模块自己生成的 md 子集（无嵌套、无链接），不追求通用。
    """
    import re
    text = html_mod.escape(text, quote=False)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def _md_table_html(header_line: str, body_lines: list) -> str:
    """| a | b | 形式的表格行 → <table>（首行表头；第二行是 --- 分隔线，调用方已剔除）。"""
    def cells(line):
        parts = [c.strip() for c in line.strip().strip('|').split('|')]
        return parts
    h = cells(header_line)
    out = ['<table>', '<thead><tr>' + ''.join(f'<th>{_inline_html(c)}</th>' for c in h) + '</tr></thead>']
    out.append('<tbody>')
    for line in body_lines:
        cs = cells(line)
        # 列数对齐表头（多余截断、不足补空，防错位）
        cs = (cs + [''] * len(h))[:len(h)]
        out.append('<tr>' + ''.join(f'<td>{_inline_html(c)}</td>' for c in cs) + '</tr>')
    out.append('</tbody></table>')
    return '\n'.join(out)


def _md_to_html(md: str) -> str:
    """本报告 md 子集 → HTML 片段（标题/表格/引用/段落）。

    只处理 generate() 会产出的结构：#/##/### 标题、| 表格（含 --- 分隔行）、
    > 引用块、普通段落行。不支持列表/链接/嵌套——超出子集的行按段落渲染。
    """
    lines = md.split('\n')
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith('|') and i + 1 < n and set(lines[i + 1].strip()) <= {'|', '-', ':', ' '} \
                and '-' in lines[i + 1]:
            # 表格块：首行表头 + 分隔行 + 数据行（连续 | 开头）
            j = i + 2
            body = []
            while j < n and lines[j].strip().startswith('|'):
                body.append(lines[j])
                j += 1
            out.append(_md_table_html(line, body))
            i = j
            continue
        if s.startswith('### '):
            out.append(f'<h3>{_inline_html(s[4:])}</h3>')
        elif s.startswith('## '):
            out.append(f'<h2>{_inline_html(s[3:])}</h2>')
        elif s.startswith('# '):
            out.append(f'<h1>{_inline_html(s[2:])}</h1>')
        elif s.startswith('>'):
            # 连续 > 行合并为一个 blockquote
            quoted = []
            while i < n and lines[i].strip().startswith('>'):
                quoted.append(lines[i].strip().lstrip('>').strip())
                i += 1
            out.append('<blockquote>' + '<br>'.join(_inline_html(q) for q in quoted) + '</blockquote>')
            continue
        else:
            out.append(f'<p>{_inline_html(s)}</p>')
        i += 1
    return '\n'.join(out)


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
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _week_bounds(iso_week: str):
    year, week = iso_week.split('-W')
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def _md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def _latest_by_key(con, source):
    """取 (source) 下每个 (appmsgid, itemidx) 的最新 stat_date 快照行 → dict。

    返回 {(appmsgid_str, itemidx_int): (stat_date, title, metrics_dict)}。
    """
    rows = con.execute(
        "SELECT appmsgid, itemidx, stat_date, title, metrics FROM snapshots "
        "WHERE source=? ORDER BY stat_date", (source,)).fetchall()
    best = {}
    for am, ix, sd, ti, m in rows:
        best[(str(am), int(ix))] = (sd, ti, json.loads(m))   # 后写=更新快照日
    return best


def _fmt_value(v, is_ratio):
    """把数值格式化为人读串（ratio 概念 ×100%，其它按原值）。"""
    if not isinstance(v, (int, float)):
        return '-'
    if is_ratio:
        return '%.1f%%' % (100 * v)
    return str(int(v)) if float(v).is_integer() else ('%.1f' % v)


def _wide_rows_for_concept(con, cname):
    """生成某概念一张宽表 → {header, rows, has_data, all_zero, missing_sources}。

    列 = [文章, appmsgid_itemidx, s1 值, s2 值, …, 差值说明列?]
    行 = 仅包含 (appmsgid, itemidx) 命中概念下**所有 series 源**都有快照的交集；
    且每个 series 字段都有数值。空交集 → has_data=False，周报层折叠。

    all_zero：宽表全部数值为 0（无信号），调用方应折叠。
    missing_sources：哪些源没覆盖到此概念（给读者诊断"为什么没数据"）。
    """
    c = metrics_mod.CONCEPTS[cname]
    series = [(k, s) for k, s in c['series'].items() if not s.get('deprecated')]
    if not series:
        return {'header': [], 'rows': [], 'has_data': False, 'all_zero': False,
                'missing_sources': []}
    is_ratio = '比例' in c['unit']
    by_source = {s['source']: _latest_by_key(con, s['source']) for _, s in series}
    keys_union = set()
    for src_map in by_source.values():
        keys_union.update(src_map)
    rows = []
    any_nonzero = False
    for key in sorted(keys_union):
        sd_a, ti_a, mm_a = by_source[series[0][1]['source']].get(key, (None, '', {}))
        all_ok = True
        cells = []
        vals = []
        for skey, s in series:
            mm = by_source[s['source']].get(key, (None, '', {}))[2]
            v = mm.get(s['field'])
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
                    va = by_source[sa['source']][key][2].get(sa['field'])
                    vb = by_source[sb['source']][key][2].get(sb['field'])
                    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                        diffs.append((sa_key, sb_key, va - vb, '%s vs %s' % (sa['window'], sb['window'])))
        title = (ti_a or '')[:36]
        row = [title, '%s_%d' % key] + cells
        if is_ratio and len(series) >= 2:
            row.append('；'.join('%s−%s=%+.1fpt' % (a, b, 100 * d) for a, b, d, _ in diffs))
        # 「全 0」行折叠（噪音过滤）：宽表只保留至少一个 series 非零的行
        if any(v != 0 for v in vals):
            rows.append(row)

    header = ['文章', 'appmsgid_itemidx'] + ['%s(%s·%d天)' % (c['display'], skey, s['window_days'])
                                               for skey, s in series]
    if is_ratio and len(series) >= 2:
        header.append('差值(百分点·两两差)')
    # 缺失源/字段诊断：哪些源完全没此概念的快照，或哪个 field 在该源任何快照都不存在
    missing_src = [sd['source'] for _, sd in series if not by_source[sd['source']]]
    missing_field = []
    if rows == [] and not missing_src:
        for skey, s in series:
            # _latest_by_key.values() 是 (sd, ti, mm) 三元组；mm 才是指标 dict
            if not any(s['field'] in row[2] for row in by_source[s['source']].values()):
                missing_field.append('%s(`%s`)' % (s['source'], s['field']))
    # 单独追踪：all rows in 交集 all-zero（被「行级」过滤掉 → has_data 假但非"无源/无字段"）
    # 区分两种情况：① union 非空但没有任何行通过 all_ok（典型：两源 appmsgid 不重叠）
    # ② 全部行因 all-zero 被过滤掉
    has_any_intersect = False
    for key in keys_union:
        all_ok = True
        for skey, s in series:
            v = by_source[s['source']].get(key, (None, '', {}))[2].get(s['field'])
            if not isinstance(v, (int, float)):
                all_ok = False
                break
        if all_ok:
            has_any_intersect = True
            break
    return {'header': header, 'rows': rows, 'has_data': bool(rows),
            'all_zero': bool(rows) and not any_nonzero,
            'missing_sources': missing_src,
            'missing_fields': missing_field,
            'all_rows_filtered': has_any_intersect and not rows and not missing_src and not missing_field,
            'no_intersect': (not has_any_intersect) and bool(keys_union) and not missing_src and not missing_field}


def cross_source_table(con, concept: str = None) -> list:
    """跨 series 对照（宽表）：每概念一张宽表。

    返回 list of {concept, display, header, rows, note, has_data, all_zero,
    missing_sources}。每概念只一块；auto_pairs 用于生成 note 中的「不可直接比」
    明细（不是再产 C(N,2) 张表）。deprecated / META_FIELDS / DEPRECATED 不参与。
    concept 过滤：concept 与 display 都接受。
    """
    out = []
    for cname, c in metrics_mod.CONCEPTS.items():
        non_dep = [(k, s) for k, s in c['series'].items() if not s.get('deprecated')]
        if len(non_dep) < 2:
            continue   # 单 series 概念不生成跨 series 对照
        if concept and concept not in (cname, c['display']):
            continue
        wide = _wide_rows_for_concept(con, cname)
        # 概念级不可比说明（用 auto_pairs 的同概念条目）
        parts = []
        for a, b in combinations([k for k, _ in non_dep], 2):
            sa, sb = c['series'][a], c['series'][b]
            win_diff = sa['window'] != sb['window']
            den_diff = sa['denominator'] != sb['denominator']
            if win_diff or den_diff:
                bits = []
                if win_diff:
                    bits.append('窗口 %s vs %s' % (sa['window'], sb['window']))
                if den_diff:
                    bits.append('分母「%s」 vs 「%s」' % (sa['denominator'], sb['denominator']))
                parts.append('`%s` vs `%s`：%s' % (a, b, '；'.join(bits)))
        note = '⚠ 同一概念「%s」多 series 不可直接比：%s' % (c['display'], '；'.join(parts))
        out.append({'concept': cname, 'display': c['display'],
                    'header': wide['header'], 'rows': wide['rows'],
                    'note': note, 'has_data': wide['has_data'],
                    'all_zero': wide.get('all_zero', False) or wide.get('all_rows_filtered', False),
                    'missing_sources': wide.get('missing_sources', []),
                    'missing_fields': wide.get('missing_fields', []),
                    'no_intersect': wide.get('no_intersect', False)})
    return out


def generate(con, iso_week: str, out_dir: str) -> dict:
    monday, sunday = _week_bounds(iso_week)
    b, e = monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')
    rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
        "AND stat_date BETWEEN ? AND ? ORDER BY stat_date", (b, e)).fetchall()
    arts = [(sd, ti, json.loads(m)) for sd, ti, m in rows]
    # 完读率 Top10（取 datacube 7 天完读率）
    by_key = {}
    for sd, ti, m in arts:
        if m.get('finish_read_rate'):
            key = (ti, sd)
            by_key[key] = m
    top = sorted(by_key.items(), key=lambda kv: -float(kv[1]['finish_read_rate']))[:10]
    def _top_cell(k, m):
        v = m.get(k)
        if not isinstance(v, (int, float)):
            return v if v != '' else '-'
        return _fmt_value(v, k in ('finish_read_rate', 'read_rate'))
    top_rows = [[kv[0][0][:40]] + [_top_cell(k, kv[1]) for k in
                  ('finish_read_rate', 'read_rate', 'total_pv', 'total_share')] for kv in top]
    lines = [f'# 公众号数据周报 {iso_week}（{b} ~ {e}）', '', calibration_note(), '']
    lines.append(f'快照文章行数：{len(arts)}（datacube 文章级，7 天窗口口径）')
    lines.append('')
    if top:
        n = min(10, len(top))
        suffix = '' if n == 10 else f'（共 {len(top)} 篇）'
        lines.append('## 完读率 Top%d%s（datacube 发表后 7 天窗口）' % (n, suffix))
        lines.append(_md_table(['文章', '完读率', '阅读率', '送达pv', '分享'], top_rows))
    else:
        lines.append('本周无 datacube 快照数据（fetch 未覆盖该周，或均为 T+1 未出）。')

    # 跨 series 对照（宽表）：折叠无数据 / 全 0 概念
    blocks = cross_source_table(con)
    cross_rows_total = 0
    has_data_concepts = [b for b in blocks if b['has_data'] and not b['all_zero']]
    folded_concepts = [b for b in blocks if (not b['has_data']) or b['all_zero']]
    if blocks:
        lines.append('')
        lines.append('## 跨 series 对照（每概念一张宽表；同概念多 series 不可直接比）')
        for blk in has_data_concepts:
            lines.append('')
            lines.append('### 概念「%s」' % blk['display'])
            lines.append(_md_table(blk['header'], blk['rows']))
            cross_rows_total += len(blk['rows'])
            if blk['note']:
                lines.append(blk['note'])
        if folded_concepts:
            # 按"为什么折叠"分组
            missing_src = [b for b in folded_concepts if not b['has_data'] and b['missing_sources']]
            missing_field = [b for b in folded_concepts
                             if not b['has_data'] and not b['missing_sources'] and b['missing_fields']]
            no_intersect = [b for b in folded_concepts if b['no_intersect']]
            all_zero_no_rows = [b for b in folded_concepts
                                if b['all_zero'] and not b['has_data']
                                and not b['missing_sources'] and not b['missing_fields']]
            zero = [b for b in folded_concepts if b['has_data'] and b['all_zero']]
            if missing_src:
                by_missing = {}
                for b in missing_src:
                    key = '+'.join(sorted(b['missing_sources']))
                    by_missing.setdefault(key, []).append(b['display'])
                parts = ['%s（缺 %s 快照）' % ('、'.join(names), miss)
                         for miss, names in by_missing.items()]
                lines.append('')
                lines.append('> **本周无可对照数据的概念**：%s。' % '；'.join(parts))
            if missing_field:
                by_mf = {}
                for b in missing_field:
                    key = '+'.join(sorted(b['missing_fields']))
                    by_mf.setdefault(key, []).append(b['display'])
                parts = ['%s（缺字段 %s）' % ('、'.join(names), mf)
                         for mf, names in by_mf.items()]
                lines.append('> **本周无可对照数据的概念**：%s。' % '；'.join(parts))
            if no_intersect:
                lines.append('> **本周无可对照数据的概念（快照 appmsgid 无交集）**：%s。'
                             % '、'.join(b['display'] for b in no_intersect))
            if all_zero_no_rows:
                lines.append('> **本周宽表无信号（全 0 行）**：%s。'
                             % '、'.join(b['display'] for b in all_zero_no_rows))
            if zero:
                lines.append('> **本周宽表无信号（全 0 概念）**：%s。'
                             % '、'.join(b['display'] for b in zero))
    else:
        lines.append('')
        lines.append('## 跨 series 对照')
        lines.append('（本周 CONCEPTS 中无多 series 概念，无需对照。）')

    # 单篇渠道构成（FR3②，30 天窗口口径）
    det = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='article_detail' "
        "ORDER BY stat_date DESC, appmsgid DESC LIMIT 15").fetchall()
    if det:
        lines.append('')
        lines.append('## 单篇渠道构成（完读率 + 渠道来源，30 天窗口口径）')
        hdr = ['文章', '发表日', '阅读人数', '完读率', '平均时长(秒)', '推荐%', '公众号消息%', '搜一搜%']

        def _pct(m, scene):
            for r in m.get('channel_breakdown') or []:
                if r.get('scene') == scene:
                    return r.get('read_pct')
            return None

        rows2 = []
        for sd, ti, m in ((a, b, json.loads(c)) for a, b, c in det):
            ruv = m.get('read_uv')
            # 跳过 read_uv=0 的"无信号"行（与折叠精神一致）
            if not isinstance(ruv, (int, float)) or ruv == 0:
                continue
            fr = m.get('finished_read_pv_ratio')
            rows2.append([
                (ti or '')[:36], m.get('publish_date', ''), ruv,
                ('%.1f%%' % (100 * fr)) if isinstance(fr, (int, float)) else '-',
                m.get('avg_article_read_time', ''),
                _pct(m, 6) if _pct(m, 6) is not None else '-',
                _pct(m, 0) if _pct(m, 0) is not None else '-',
                _pct(m, 7) if _pct(m, 7) is not None else '-'])
        lines.append(_md_table(hdr, rows2))
        if rows2:
            lines.append('（渠道占比分母=「全部」渠道阅读人数；同一用户多渠道重复计数，占比之和可 >100%）')
        else:
            lines.append('（本周无 read_uv > 0 的单篇快照。）')
    md = '\n'.join(lines) + '\n'
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f'weekly-{iso_week}.md')
    open(md_path, 'w').write(md)
    html_path = os.path.join(out_dir, f'weekly-{iso_week}.html')
    open(html_path, 'w').write(_HTML_TEMPLATE.format(
        title=f'mpdt 周报 {iso_week}', body=_md_to_html(md)))
    assert open(md_path).read() == md and '口径说明' in open(md_path).read()
    # 模板化 HTML 读回断言（P3：机制生效而非碰巧）。注意空库周报无表格——
    # <table> 存在性由带数据的 TestGenerate/TestHtmlTemplate 覆盖，此处只断言不变量。
    h = open(html_path).read()
    assert f'<title>mpdt 周报 {iso_week}</title>' in h and '<pre>' not in h \
        and '口径说明' in h, 'HTML 模板化断言失败'
    return {'md': md_path, 'html': html_path, 'rows': len(arts),
            'cross_rows': cross_rows_total,
            'concepts_compared': [b['concept'] for b in has_data_concepts],
            'concepts_empty': [b['concept'] for b in folded_concepts]}
