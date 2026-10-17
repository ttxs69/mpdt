"""指标口径注册表（唯一事实源）：每个指标登记
(规范名, 源字段名, 所属源, 窗口口径, 分母, 单位, 是否跨源可比)。

人读文档（周报口径说明 / README 指标口径节 / `mpdt metrics list`）全部由本表生成，
不在 report.py / README 里二次硬编码——改口径只改这里。

源窗口速查：
  publish_list     实时累计（拉取时点全量，无窗口）
  datacube_article 发表后 7 天窗口，T+1 出数（refdate 为文章口径日）
  article_detail   发表后 30 天窗口（页面口径自 2025-11-01 起）
"""
from collections import namedtuple

MetricDef = namedtuple('MetricDef',
                       'name field source window denominator unit comparable note')
# name 规范名 | field 源字段名（=snapshots.metrics JSON 键名）
# window 窗口口径 | denominator 分母 | unit 单位
# comparable 是否跨源可比（False=与其它源同名/近义指标不可直接比）

W_PL = '实时累计（拉取时点全量）'
W_DC = '发表后 7 天窗口，T+1 出数'
W_AD = '发表后 30 天窗口'

METRICS = (
    # ---- publish_list（发表列表，实时累计） ----
    MetricDef('阅读数', 'read_num', 'publish_list', W_PL, '-', '人次', False,
              '实时累计；与 datacube total_pv / article_detail read_uv 窗口、分母均不同'),
    MetricDef('分享数', 'share_num', 'publish_list', W_PL, '-', '次', False,
              '实时累计；与 datacube total_share / article_detail share_uv 不可直接比'),
    MetricDef('点赞(在看)数', 'like_num', 'publish_list', W_PL, '-', '次', False,
              '实时累计；与 article_detail like_cnt 不可直接比'),
    MetricDef('旧版点赞数', 'old_like_num', 'publish_list', W_PL, '-', '次', False, ''),
    MetricDef('评论数', 'comment_num', 'publish_list', W_PL, '-', '条', False,
              '实时累计；与 article_detail comment_cnt 不可直接比'),

    # ---- datacube_article（内容分析-文章级，7 天窗口 T+1） ----
    MetricDef('统计日', 'refdate', 'datacube_article', W_DC, '-', '日期', True,
              '文章口径日（快照行 stat_date 是拉取日，两者不同）'),
    MetricDef('完读率(7天·阅读人数口径)', 'finish_read_rate', 'datacube_article', W_DC,
              '7 天窗口内阅读人数', '比例(0-1)', False,
              '与 article_detail finished_read_pv_ratio 同叫「完读率」但窗口(7天/30天)与分母(人数/pv)均不同，数值不可直接比'),
    MetricDef('阅读率', 'read_rate', 'datacube_article', W_DC, '送达人数(send_succ)',
              '比例(0-1)', False, ''),
    MetricDef('阅读次数', 'total_pv', 'datacube_article', W_DC, '-', '次', False,
              '实测含全渠道阅读（推荐/搜一搜等），量级≈单篇页 read_uv；与 publish_list read_num 口径不同'),
    MetricDef('完读次数', 'total_read_finish', 'datacube_article', W_DC, '-', '次', False, ''),
    MetricDef('分享次数', 'total_share', 'datacube_article', W_DC, '-', '次', False, ''),
    MetricDef('送达人数', 'send_succ', 'datacube_article', W_DC, '-', '人', False, ''),

    # ---- article_detail（单篇渠道页，30 天窗口） ----
    MetricDef('阅读人数(全渠道去重)', 'read_uv', 'article_detail', W_AD, '-', '人', False,
              '「全部」渠道去重口径；与 datacube total_pv(次数)、publish_list read_num(实时累计) 不可直接比'),
    MetricDef('完读率(30天·pv口径)', 'finished_read_pv_ratio', 'article_detail', W_AD,
              '阅读次数(pv)', '比例(0-1)', False,
              '与 datacube finish_read_rate 同叫「完读率」但窗口与分母均不同，数值不可直接比'),
    MetricDef('分享人数', 'share_uv', 'article_detail', W_AD, '-', '人', False, ''),
    MetricDef('点赞数', 'like_cnt', 'article_detail', W_AD, '-', '次', False, ''),
    MetricDef('评论数', 'comment_cnt', 'article_detail', W_AD, '-', '条', False, ''),
    MetricDef('收藏人数', 'collection_uv', 'article_detail', W_AD, '-', '人', False, ''),
    MetricDef('阅读后关注人数', 'follow_after_read_uv', 'article_detail', W_AD, '-', '人', False, ''),
    MetricDef('听读人数', 'listen_uv', 'article_detail', W_AD, '-', '人', False, ''),
    MetricDef('听读次数', 'listen_pv', 'article_detail', W_AD, '-', '次', False, ''),
    MetricDef('在看数', 'zaikan_cnt', 'article_detail', W_AD, '-', '次', False, ''),
    MetricDef('赞赏金额', 'praise_money', 'article_detail', W_AD, '-', '上游金额单位', False, ''),
    MetricDef('平均阅读时长', 'avg_article_read_time', 'article_detail', W_AD, '-', '秒', False, ''),
    MetricDef('发表日', 'publish_date', 'article_detail', W_AD, '-', '日期', True, ''),
    MetricDef('窗口天数', 'window_days', 'article_detail', W_AD, '-', '天', True,
              'mpdt 写入的派生列（页面固定 30 天）'),
    MetricDef('全渠道阅读人数(汇总)', 'total_read_user', 'article_detail', W_AD, '-', '人', False,
              'mpdt 派生列：「全部」渠道逐日 read_user 求和'),
    MetricDef('渠道构成', 'channel_breakdown', 'article_detail', W_AD, '「全部」渠道阅读人数',
              'JSON', False,
              'mpdt 派生列；同一用户多渠道重复计数 → 占比之和可 >100%（仅「全部」去重）'),
)

SOURCES = ('publish_list', 'datacube_article', 'article_detail')

SOURCE_DESC = {
    'publish_list': '发表列表（/cgi-bin/appmsgpublish）',
    'datacube_article': 'datacube 内容分析-文章级（/misc/datacubequery tmpl=17）',
    'article_detail': '单篇渠道页（/misc/appmsganalysis detailpage）',
}

# 跨源易混淆配对：report 跨源对照表与文档据此生成提醒
CROSS_SOURCE_PAIRS = (
    ('finish_read_rate', 'datacube_article', 'finished_read_pv_ratio', 'article_detail',
     '同为「完读率」：7天窗口·阅读人数分母 vs 30天窗口·pv 分母，数值不可直接比'),
    ('total_pv', 'datacube_article', 'read_uv', 'article_detail',
     '同为「阅读量级」：次数口径 vs 去重人数口径'),
    ('read_num', 'publish_list', 'read_uv', 'article_detail',
     '实时累计 vs 30 天窗口去重人数'),
)


def all_metrics() -> tuple:
    return METRICS


def by_source(source: str) -> list:
    return [m for m in METRICS if m.source == source]


def lookup(field: str, source: str = None):
    """按源字段名（=snapshots.metrics JSON 键）查口径；source 限定则要求匹配。"""
    hits = [m for m in METRICS if m.field == field]
    if source is not None:
        hits = [m for m in hits if m.source == source]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def to_dicts(query: str = None) -> list:
    """注册表 → dict 列表（`mpdt metrics list --json` 的 data 体）。

    query 给定则按 规范名/字段名/源名 子串过滤（查『这个字段是什么口径』）。
    """
    q = (query or '').strip().lower()
    out = []
    for m in METRICS:
        if q and q not in (m.name + m.field + m.source).lower():
            continue
        out.append({'name': m.name, 'field': m.field, 'source': m.source,
                    'window': m.window, 'denominator': m.denominator,
                    'unit': m.unit, 'comparable': m.comparable,
                    **({'note': m.note} if m.note else {})})
    return out


def calibration_note() -> str:
    """周报文首口径说明（由注册表生成；report.generate 直接使用）。"""
    lines = ['> **口径说明（由 mpdt/metrics.py 注册表生成；同名指标口径不同、不可直接相比）**']
    for src in SOURCES:
        ms = by_source(src)
        items = '；'.join(
            '%s=%s（分母：%s）' % (m.field, m.name, m.denominator) for m in ms)
        lines.append('> - **%s**（%s，%s）：%s' % (src, SOURCE_DESC[src], ms[0].window, items))
    lines.append('> 跨源比较前先对齐窗口与分母，否则结论可能反向（对照表见「跨源对照」节）。')
    return '\n'.join(lines)


def readme_section() -> str:
    """README『指标口径』节（由注册表生成；改表后重新生成本节保持同步）。"""
    out = ['| 规范名 | 源字段 | 所属源 | 窗口口径 | 分母 | 单位 | 跨源可比 |',
           '|---|---|---|---|---|---|---|']
    for m in METRICS:
        out.append('| %s | `%s` | %s | %s | %s | %s | %s |' % (
            m.name, m.field, m.source, m.window, m.denominator, m.unit,
            '是' if m.comparable else '否'))
    out.append('')
    out.append('易混淆跨源配对（数值不可直接比）：')
    for fa, sa, fb, sb, why in CROSS_SOURCE_PAIRS:
        out.append('- `%s`(%s) vs `%s`(%s)：%s' % (fa, sa, fb, sb, why))
    out.append('')
    out.append('> 本节由 `mpdt/metrics.py` 注册表生成'
               '（`python3 -c "from mpdt.metrics import readme_section; print(readme_section())"`），'
               '改口径请改注册表后同步本节，或运行 `mpdt metrics list` 查看最新口径。')
    return '\n'.join(out)
