"""周报生成（FR5）：Markdown + HTML；文首口径说明（R4）。"""
import datetime as dt
import html as html_mod

CALIBRATION_NOTE = (
    "> **口径说明（R28 跨源实测校正，同名指标口径不同、不可直接相比）**：\n"
    "> 1) **datacube「内容分析-文章级」**：**发表后 7 天窗口**，数据 T+1 口径；"
    "total_pv=**全渠道阅读次数**（含推荐/搜一搜——实测 9/23 文 total_pv 208 ≈ 单篇页全渠道 "
    "read_uv 190，同量级），read_rate=消息渠道阅读人数/送达人数，finish_read_rate 为该表口径的完成阅读率。\n"
    "> 2) **单篇渠道页（article_detail）**：**发表后 30 天窗口**；read_uv=全渠道阅读人数，"
    "finished_read_pv_ratio=完读率(pv 口径)，channel_breakdown=逐渠道人数构成"
    "（推荐/搜一搜/公众号消息…；同一用户多渠道重复计数 → 占比之和可 >100%）。\n"
    "> 3) **publish_list read_num**：实时累计全量口径。\n"
    "> 三源体量相近但分母/窗口不同；跨源比较前先对齐窗口与分母，否则结论可能反向。")


def _week_bounds(iso_week: str):
    year, week = iso_week.split('-W')
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def _md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def generate(con, iso_week: str, out_dir: str) -> dict:
    monday, sunday = _week_bounds(iso_week)
    b, e = monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')
    rows = con.execute(
        "SELECT stat_date, title, metrics FROM snapshots WHERE source='datacube_article' "
        "AND stat_date BETWEEN ? AND ? ORDER BY stat_date", (b, e)).fetchall()
    import json
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
    lines = [f'# 公众号数据周报 {iso_week}（{b} ~ {e}）', '', CALIBRATION_NOTE, '']
    lines.append(f'快照文章行数：{len(arts)}（datacube 文章级，7 天窗口口径）')
    lines.append('')
    if top:
        lines.append('## 完读率 Top10（datacube 发表后 7 天窗口）')
        lines.append(_md_table(['文章', '完读率', '阅读率', '送达pv', '分享'], top_rows))
    else:
        lines.append('本周无 datacube 快照数据（fetch 未覆盖该周，或均为 T+1 未出）。')

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
    return {'md': md_path, 'html': html_path, 'rows': len(arts)}

import os  # noqa: E402  (generate 上方使用，集中放头部亦可)
