"""周报生成（FR5）：Markdown + HTML；文首口径说明（R4）——由 metrics.py 注册表生成。

跨源对照表：同一篇文章的 datacube 完读率(7天) vs article_detail 完读率(30天)并排，
自动标注窗口差异与差值，并提醒不可直接比。
"""
import datetime as dt
import html as html_mod
import json
import os

from . import metrics as metrics_mod
from .metrics import calibration_note   # 口径说明唯一来源：注册表，不再硬编码

# 跨源对照的配对定义（字段级 → 也登记在 metrics.CROSS_SOURCE_PAIRS，这里绑定报告用列）
_PAIR = ('finish_read_rate', 'datacube_article', 'finished_read_pv_ratio', 'article_detail')


def _week_bounds(iso_week: str):
    year, week = iso_week.split('-W')
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def _md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def cross_source_table(con) -> tuple:
    """跨源对照：datacube 完读率(7天窗口) vs article_detail 完读率(30天窗口) 并排。

    匹配键 = (appmsgid, itemidx)，各取最新 stat_date 快照行。
    返回 (header, rows, note)；rows 每行差值列=百分比点的原始差（dc-ad）。
    """
    def _latest(source):
        rows = con.execute(
            "SELECT appmsgid, itemidx, stat_date, title, metrics FROM snapshots "
            "WHERE source=? ORDER BY stat_date", (source,)).fetchall()
        best = {}
        for am, ix, sd, ti, m in rows:
            best[(str(am), int(ix))] = (sd, ti, json.loads(m))   # 后写=更新快照日
        return best

    dc, ad = _latest(_PAIR[1]), _latest(_PAIR[3])
    f_dc, f_ad = _PAIR[0], _PAIR[2]
    m_dc = metrics_mod.lookup(f_dc, _PAIR[1])
    m_ad = metrics_mod.lookup(f_ad, _PAIR[3])
    header = ['文章', 'appmsgid_itemidx', 'datacube 完读率(7天)', '单篇页完读率(30天)',
              '差值(百分点)', '窗口差异']
    rows = []
    for key in sorted(set(dc) & set(ad)):
        sd_dc, ti_dc, mm_dc = dc[key]
        sd_ad, ti_ad, mm_ad = ad[key]
        v_dc, v_ad = mm_dc.get(f_dc), mm_ad.get(f_ad)
        if not (isinstance(v_dc, (int, float)) and isinstance(v_ad, (int, float))):
            continue
        title = (ti_dc or ti_ad or '')[:36]
        rows.append([title, '%s_%d' % key,
                     '%.1f%%' % (100 * v_dc), '%.1f%%' % (100 * v_ad),
                     '%+.1f' % (100 * (v_dc - v_ad)),
                     '%s vs %s' % (m_dc.window, m_ad.window)])
    note = ('（⚠ 不可直接比：`%s`=%s·分母「%s」；`%s`=%s·分母「%s」——'
            '窗口与分母均不同，差值仅提示两口径的观察差异，非同一口径变化。）' % (
                f_dc, m_dc.window, m_dc.denominator,
                f_ad, m_ad.window, m_ad.denominator))
    return header, rows, note


def generate(con, iso_week: str, out_dir: str) -> dict:
    monday, sunday = _week_bounds(iso_week)
    b, e = monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')
    rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
        "AND stat_date BETWEEN ? AND ? ORDER BY stat_date", (b, e)).fetchall()
    arts = [(sd, ti, json.loads(m)) for sd, ti, m in rows]
    # 完读率 Top10（按 appmsgid+itemidx 去重，取最新快照日的行）
    by_key = {}
    for sd, ti, m in arts:
        if m.get('finish_read_rate'):
            key = (ti, sd)                      # sd 参与 key 保多日快照
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

    # 跨源对照（同一篇文章：7 天完读率 vs 30 天完读率并排 + 窗口差异 + 差值）
    hdr, xrows, xnote = cross_source_table(con)
    lines.append('')
    lines.append('## 跨源对照：datacube(7天) vs 单篇页(30天) 完读率')
    if xrows:
        lines.append(_md_table(hdr, xrows))
        lines.append(xnote)
    else:
        lines.append('（本周无跨源可对照文章：需同一 (appmsgid,itemidx) 同时存在 '
                     'datacube_article 与 article_detail 两个源的快照。）')

    # 单篇渠道构成（FR3②，30 天窗口口径；推荐/搜一搜占比是主路径放量的核心读数）
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
    # 写后读回（T2）
    assert open(md_path).read() == md and '口径说明' in open(md_path).read()
    return {'md': md_path, 'html': html_path, 'rows': len(arts), 'cross_rows': len(xrows)}
