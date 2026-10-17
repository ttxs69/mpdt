"""周报生成（FR5）：Markdown + HTML；文首口径说明（R4）——由 metrics.py CONCEPTS 生成。

跨 series 对照：对任何含 ≥2 series 的概念（完读率 / 阅读量 / 分享 / 点赞 / 评论 / 听读）
各生成一张并排表，自动标注窗口/分母差异与差值，提醒不可直接比。
"""
import datetime as dt
import html as html_mod
import json
import os

from . import metrics as metrics_mod
from .metrics import calibration_note, auto_pairs


def _week_bounds(iso_week: str):
    year, week = iso_week.split('-W')
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def _md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def _latest_by_key(con, source):
    """取 (source) 下每个 (appmsgid, itemidx) 的最新 stat_date 快照行 → dict。"""
    rows = con.execute(
        "SELECT appmsgid, itemidx, stat_date, title, metrics FROM snapshots "
        "WHERE source=? ORDER BY stat_date", (source,)).fetchall()
    best = {}
    for am, ix, sd, ti, m in rows:
        best[(str(am), int(ix))] = (sd, ti, json.loads(m))   # 后写=更新快照日
    return best


def _rows_for_pair(con, concept, sa_key, sb_key, fld_a, fld_b):
    """两条 series 的并排 + 差值 + 窗口标注。ratio 概念按百分比格式，否则原值。

    concept: 概念名（用于决定格式：'比例' 单位 → ×100；其它 → 原值）。
    """
    is_ratio = '比例' in metrics_mod.CONCEPTS[concept]['unit']
    da, db = _latest_by_key(con, sa_key), _latest_by_key(con, sb_key)
    rows = []
    for key in sorted(set(da) & set(db)):
        sd_a, ti_a, mm_a = da[key]
        sd_b, ti_b, mm_b = db[key]
        v_a, v_b = mm_a.get(fld_a), mm_b.get(fld_b)
        if not (isinstance(v_a, (int, float)) and isinstance(v_b, (int, float))):
            continue
        title = (ti_a or ti_b or '')[:36]
        if is_ratio:
            ca_str = '%.1f%%' % (100 * v_a)
            cb_str = '%.1f%%' % (100 * v_b)
            diff = '%+.1f' % (100 * (v_a - v_b))
        else:
            ca_str = str(int(v_a)) if float(v_a).is_integer() else ('%.1f' % v_a)
            cb_str = str(int(v_b)) if float(v_b).is_integer() else ('%.1f' % v_b)
            diff = '%+g' % (v_a - v_b)
        rows.append([title, '%s_%d' % key, ca_str, cb_str, diff,
                     '%s vs %s' % (W_OF[sa_key], W_OF[sb_key])])
    return rows


W_OF = {metrics_mod.SRC_PL: '实时累计', metrics_mod.SRC_DC: '7 天窗口', metrics_mod.SRC_AD: '30 天窗口'}


def cross_source_table(con, concept: str = None) -> list:
    """跨 series 对照：返回 list of {concept, display, header, rows, note}。

    不再硬编码只对照完读率：对任何含 ≥2 series 的概念都生成一张并排表。
    concept 给定时只返回该概念；否则返回全部。
    """
    out = []
    for cname, a, b, why in auto_pairs():
        c = metrics_mod.CONCEPTS[cname]
        if concept and concept not in (cname, c['display']):
            continue
        sa, sb = c['series'][a], c['series'][b]
        rows = _rows_for_pair(con, cname, sa['source'], sb['source'],
                              sa['field'], sb['field'])
        if not rows and not concept:
            # 周报里无交集的也展示（标注空），让读者看到"为何该周无对照"
            pass
        is_ratio = '比例' in c['unit']
        header = ['文章', 'appmsgid_itemidx',
                  '%s(%s)' % (c['display'], a),
                  '%s(%s)' % (c['display'], b),
                  '差值(百分点)' if is_ratio else '差值',
                  '窗口差异']
        note = ('（⚠ 不可直接比：概念「%s」series `%s`(%s·字段 `%s`·分母「%s」) vs series `%s`(%s·字段 `%s`·分母「%s」)——%s）' % (
            c['display'],
            a, sa['window'], sa['field'], sa['denominator'],
            b, sb['window'], sb['field'], sb['denominator'], why))
        out.append({'concept': cname, 'display': c['display'],
                    'pair': (a, b), 'header': header, 'rows': rows, 'note': note})
    return out


def generate(con, iso_week: str, out_dir: str) -> dict:
    monday, sunday = _week_bounds(iso_week)
    b, e = monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')
    rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
        "AND stat_date BETWEEN ? AND ? ORDER BY stat_date", (b, e)).fetchall()
    arts = [(sd, ti, json.loads(m)) for sd, ti, m in rows]
    # 完读率 Top10（取 datacube 7 天完读率，跨 series 对照里也会出 dc7/ad30 并排）
    by_key = {}
    for sd, ti, m in arts:
        if m.get('finish_read_rate'):
            key = (ti, sd)
            by_key[key] = m
    top = sorted(by_key.items(), key=lambda kv: -float(kv[1]['finish_read_rate']))[:10]
    top_rows = [[kv[0][0][:40]] + [kv[1].get(k, '') for k in
                  ('finish_read_rate', 'read_rate', 'total_pv', 'total_share')] for kv in top]
    lines = [f'# 公众号数据周报 {iso_week}（{b} ~ {e}）', '', calibration_note(), '']
    lines.append(f'快照文章行数：{len(arts)}（datacube 文章级，7 天窗口口径）')
    lines.append('')
    if top:
        lines.append('## 完读率 Top10（datacube 发表后 7 天窗口）')
        lines.append(_md_table(['文章', '完读率', '阅读率', '送达pv', '分享'], top_rows))
    else:
        lines.append('本周无 datacube 快照数据（fetch 未覆盖该周，或均为 T+1 未出）。')

    # 跨 series 对照：所有 ≥2 series 概念各自一张并排表
    blocks = cross_source_table(con)
    cross_rows_total = 0
    if blocks:
        lines.append('')
        lines.append('## 跨 series 对照（同概念多 series 不可直接比）')
        for blk in blocks:
            lines.append('')
            lines.append('### 概念「%s」: series `%s` vs `%s`' % (
                blk['display'], blk['pair'][0], blk['pair'][1]))
            if blk['rows']:
                lines.append(_md_table(blk['header'], blk['rows']))
                cross_rows_total += len(blk['rows'])
            else:
                lines.append('（本周无跨 series 可对照行：需 (appmsgid,itemidx) 同时存在两源快照。）')
            lines.append(blk['note'])
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
            fr = m.get('finished_read_pv_ratio')
            rows2.append([
                (ti or '')[:36], m.get('publish_date', ''), m.get('read_uv', ''),
                ('%.1f%%' % (100 * fr)) if isinstance(fr, (int, float)) else '-',
                m.get('avg_article_read_time', ''),
                _pct(m, 6) if _pct(m, 6) is not None else '-',
                _pct(m, 0) if _pct(m, 0) is not None else '-',
                _pct(m, 7) if _pct(m, 7) is not None else '-'])
        lines.append(_md_table(hdr, rows2))
        lines.append('（渠道占比分母=「全部」渠道阅读人数；同一用户多渠道重复计数，占比之和可 >100%）')
    md = '\n'.join(lines) + '\n'
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f'weekly-{iso_week}.md')
    open(md_path, 'w').write(md)
    html_path = os.path.join(out_dir, f'weekly-{iso_week}.html')
    open(html_path, 'w').write(
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        f'<title>mpdt 周报 {iso_week}</title></head><body><pre>{html_mod.escape(md)}</pre></body></html>')
    assert open(md_path).read() == md and '口径说明' in open(md_path).read()
    return {'md': md_path, 'html': html_path, 'rows': len(arts),
            'cross_rows': cross_rows_total,
            'concepts_compared': [b['concept'] for b in blocks]}
