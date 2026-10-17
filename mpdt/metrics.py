"""指标口径注册表（语义层）：以"概念 + 系列"为身份，窗口是身份的一部分。

参考 dbt metrics / MetricFlow / Prometheus 命名哲学：
  - 同一业务概念（完读率 / 阅读量 / 分享 / …）在多源各有一个 series；
  - series_key 由源缩写 + 窗口天数拼出（dc7 / ad30 / pl），是 series 的稳定标识；
  - 易混淆配对 = 同一概念下 ≥2 个 series，自动从 window/denominator 差异生成 why。

源窗口速查：
  publish_list     实时累计（拉取时点全量，无窗口）
  datacube_article 发表后 7 天窗口，T+1 出数（refdate 为文章口径日）
  article_detail   发表后 30 天窗口（页面口径自 2025-11-01 起）

唯一真相源：本模块的 CONCEPTS；report / CLI / README / CSV 元数据全部由本模块派生。
"""
from itertools import combinations

# 源缩写（series_key 后缀） & 窗口字面量
SRC_PL = 'publish_list'
SRC_DC = 'datacube_article'
SRC_AD = 'article_detail'

W_PL = '实时累计（拉取时点全量）'
W_DC = '发表后 7 天窗口，T+1 出数'
W_AD = '发表后 30 天窗口'

WIN_DAYS = {SRC_PL: 0, SRC_DC: 7, SRC_AD: 30}   # pl=0（无窗口）仅用于 series_key 后缀与 why 文本
SERIES_SUFFIX = {SRC_PL: 'pl', SRC_DC: 'dc7', SRC_AD: 'ad30'}

SOURCE_DESC = {
    SRC_PL: '发表列表（/cgi-bin/appmsgpublish）',
    SRC_DC: 'datacube 内容分析-文章级（/misc/datacubequery tmpl=17）',
    SRC_AD: '单篇渠道页（/misc/appmsganalysis detailpage）',
}

# ── 概念分类树 ──────────────────────────────────────────────
# 概念 canonical_name → {
#   display, unit, note,
#   series: { series_key: { source, field, window, window_days, denominator, note } }
# }
#
# series_key = f"{concept_canonical}@{series_key_short}"，实际写入
# 的 series_key 形如 'dc7' / 'ad30' / 'pl'（不带概念前缀，概念已由外层 dict key 表达）。
#
# 命名哲学：series_key 是"该概念在某个 (源, 窗口) 切片下的稳定标识符"；
# 不同概念下相同的源+窗口也可能撞 series_key，所以存取值一律走
# `CONCEPTS[cname]['series'][series_key]`，不要把 series_key 当全局唯一键。
CONCEPTS = {
    'read_completion': {
        'display': '完读率', 'unit': '比例(0-1)', 'note': '完读率是同一概念下的两个口径，窗口与分母均不同',
        'series': {
            'dc7': {'source': SRC_DC, 'field': 'finish_read_rate', 'window': W_DC, 'window_days': 7,
                    'denominator': '7 天窗口内阅读人数', 'note': 'datacube 7 天窗口·阅读人数分母'},
            'ad30': {'source': SRC_AD, 'field': 'finished_read_pv_ratio', 'window': W_AD, 'window_days': 30,
                     'denominator': '阅读次数(pv)', 'note': '单篇页 30 天窗口·pv 分母'},
        },
    },
    'read_volume': {
        'display': '阅读量', 'unit': '人次/次/人（按 series 而定）', 'note': '三个 series 口径不同：实时累计 vs 7 天 pv vs 30 天 uv',
        'series': {
            'pl':   {'source': SRC_PL, 'field': 'read_num', 'window': W_PL, 'window_days': 0,
                     'denominator': '-', 'note': 'publish_list 实时累计，无窗口无去重'},
            'dc7':  {'source': SRC_DC, 'field': 'total_pv', 'window': W_DC, 'window_days': 7,
                     'denominator': '-', 'note': 'datacube 7 天窗口·全渠道阅读次数（≈量级）'},
            'ad30': {'source': SRC_AD, 'field': 'read_uv', 'window': W_AD, 'window_days': 30,
                     'denominator': '「全部」渠道去重', 'note': '单篇页 30 天窗口·全渠道去重阅读人数'},
        },
    },
    'share': {
        'display': '分享', 'unit': '次/人（按 series 而定）',
        'series': {
            'pl':   {'source': SRC_PL, 'field': 'share_num', 'window': W_PL, 'window_days': 0,
                     'denominator': '-', 'note': '实时累计分享次数'},
            'dc7':  {'source': SRC_DC, 'field': 'total_share', 'window': W_DC, 'window_days': 7,
                     'denominator': '-', 'note': 'datacube 7 天窗口·分享次数'},
            'ad30': {'source': SRC_AD, 'field': 'share_uv', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·分享人数'},
        },
    },
    'like': {
        'display': '点赞(在看)', 'unit': '次',
        'series': {
            'pl':   {'source': SRC_PL, 'field': 'like_num', 'window': W_PL, 'window_days': 0,
                     'denominator': '-', 'note': '实时累计点赞数'},
            'pl2':  {'source': SRC_PL, 'field': 'old_like_num', 'window': W_PL, 'window_days': 0,
                     'denominator': '-', 'note': '旧版字段（保留），口径与 like_num 相当'},
            'ad30': {'source': SRC_AD, 'field': 'like_cnt', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·点赞数'},
            'ad30z':{'source': SRC_AD, 'field': 'zaikan_cnt', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·「在看」数（与点赞同概念不同命名）'},
        },
    },
    'comment': {
        'display': '评论', 'unit': '条',
        'series': {
            'pl':   {'source': SRC_PL, 'field': 'comment_num', 'window': W_PL, 'window_days': 0,
                     'denominator': '-', 'note': '实时累计评论数'},
            'ad30': {'source': SRC_AD, 'field': 'comment_cnt', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·评论数'},
        },
    },
    'delivery': {
        'display': '送达人数', 'unit': '人',
        'series': {
            'dc7': {'source': SRC_DC, 'field': 'send_succ', 'window': W_DC, 'window_days': 7,
                    'denominator': '-', 'note': 'datacube 7 天窗口·送达人数（分母=送达）'},
        },
    },
    'finish_count': {
        'display': '完读次数', 'unit': '次',
        'series': {
            'dc7': {'source': SRC_DC, 'field': 'total_read_finish', 'window': W_DC, 'window_days': 7,
                    'denominator': '-', 'note': 'datacube 7 天窗口·完读次数'},
        },
    },
    'read_rate': {
        'display': '阅读率', 'unit': '比例(0-1)',
        'series': {
            'dc7': {'source': SRC_DC, 'field': 'read_rate', 'window': W_DC, 'window_days': 7,
                    'denominator': '送达人数(send_succ)', 'note': 'datacube 阅读 pv / 送达'},
        },
    },
    'follow_after_read': {
        'display': '阅读后关注人数', 'unit': '人',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'follow_after_read_uv', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·阅读后关注人数'},
        },
    },
    'collection': {
        'display': '收藏人数', 'unit': '人',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'collection_uv', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·收藏人数'},
        },
    },
    'listen': {
        'display': '听读', 'unit': '人/次',
        'series': {
            'ad30u': {'source': SRC_AD, 'field': 'listen_uv', 'window': W_AD, 'window_days': 30,
                      'denominator': '-', 'note': '单篇页 30 天窗口·听读人数'},
            'ad30p': {'source': SRC_AD, 'field': 'listen_pv', 'window': W_AD, 'window_days': 30,
                      'denominator': '-', 'note': '单篇页 30 天窗口·听读次数'},
        },
    },
    'avg_read_time': {
        'display': '平均阅读时长', 'unit': '秒',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'avg_article_read_time', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·平均阅读时长（秒）'},
        },
    },
    'praise_money': {
        'display': '赞赏金额', 'unit': '上游金额单位',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'praise_money', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·赞赏金额'},
        },
    },
    'refdate': {
        'display': '文章口径日', 'unit': '日期',
        'series': {
            'dc7': {'source': SRC_DC, 'field': 'refdate', 'window': W_DC, 'window_days': 7,
                    'denominator': '-', 'note': '文章口径日（快照行 stat_date 是拉取日，两者不同）'},
        },
    },
    'publish_date': {
        'display': '发表日', 'unit': '日期',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'publish_date', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': '单篇页 30 天窗口·文章发表日'},
        },
    },
    'window_days': {
        'display': '窗口天数', 'unit': '天',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'window_days', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': 'mpdt 写入的派生列（页面固定 30 天）'},
        },
    },
    'total_read_user': {
        'display': '全渠道阅读人数(汇总)', 'unit': '人',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'total_read_user', 'window': W_AD, 'window_days': 30,
                     'denominator': '-', 'note': 'mpdt 派生列：「全部」渠道逐日 read_user 求和'},
        },
    },
    'channel_breakdown': {
        'display': '渠道构成', 'unit': 'JSON',
        'series': {
            'ad30': {'source': SRC_AD, 'field': 'channel_breakdown', 'window': W_AD, 'window_days': 30,
                     'denominator': '「全部」渠道阅读人数',
                     'note': 'mpdt 派生列；同一用户多渠道重复计数 → 占比之和可 >100%（仅「全部」去重）'},
        },
    },
}

SOURCES = (SRC_PL, SRC_DC, SRC_AD)


# ── 派生 API（从 CONCEPTS 推导，调用方只读这一套） ──────────

def concepts() -> dict:
    """返回整个概念树（不可变视图，调用方勿改）。"""
    return CONCEPTS


def all_series():
    """展开 CONCEPTS → 平铺 series 元组流（每个 (concept, series_key, series_dict)）。"""
    for cname, c in CONCEPTS.items():
        for skey, s in c['series'].items():
            yield cname, skey, s


def find_series(field: str = None, source: str = None):
    """按 field 查 series（field 在 (source, field) 上唯一）；可同时按 source 限定。"""
    for cname, skey, s in all_series():
        if field is not None and s['field'] != field:
            continue
        if source is not None and s['source'] != source:
            continue
        return cname, skey, s
    return None


def auto_pairs():
    """易混淆配对：同概念 ≥2 series 即一对；why 由 window/denominator 差异自动生成。

    返回 tuple of (concept_name, series_a_key, series_b_key, why_text)。
    """
    out = []
    for cname, c in CONCEPTS.items():
        keys = list(c['series'])
        if len(keys) < 2:
            continue
        for a, b in combinations(keys, 2):
            sa, sb = c['series'][a], c['series'][b]
            diffs = []
            if sa['window'] != sb['window']:
                diffs.append('窗口 %s vs %s' % (sa['window'], sb['window']))
            if sa['denominator'] != sb['denominator']:
                diffs.append('分母「%s」 vs 「%s」' % (sa['denominator'], sb['denominator']))
            if not diffs:
                diffs.append('同窗口·同分母（仍按概念聚合）')
            why = '同一概念「%s」两个 series：%s，数值不可直接比' % (
                c['display'], '；'.join(diffs))
            out.append((cname, a, b, why))
    return tuple(out)


# ── 人读 / 文档派生 ─────────────────────────────────────────

def to_concepts_view(query: str = None) -> list:
    """按概念分组的人读视图（`mpdt metrics` data 体；`mpdt metrics <query>` 过滤）。

    每个概念节点：{ concept, display, unit, series: [{key, source, field, window,
    window_days, denominator, note}] }。
    query 在 concept/display/source/field 子串上匹配；任一 series 命中即保留整个概念。
    """
    q = (query or '').strip().lower()
    out = []
    for cname, c in CONCEPTS.items():
        series_out = []
        for skey, s in c['series'].items():
            series_out.append({'key': skey, 'source': s['source'], 'field': s['field'],
                               'window': s['window'], 'window_days': s['window_days'],
                               'denominator': s['denominator'], 'note': s['note']})
        hay = ' '.join([cname, c['display'], c.get('unit', '')] +
                       [s['source'] + ' ' + s['field'] for s in c['series'].values()]).lower()
        if q and q not in hay:
            continue
        out.append({'concept': cname, 'display': c['display'], 'unit': c['unit'],
                    **({'note': c['note']} if c.get('note') else {}),
                    'series': series_out})
    return out


def calibration_note() -> str:
    """周报文首口径说明（由 CONCEPTS 生成）。"""
    lines = ['> **口径说明（由 mpdt/metrics.py 概念树生成；同概念多 series 窗口/分母不同，数值不可直接比）**']
    for src in SOURCES:
        # 按源列概念，提示该源在本周报中的全部 series
        rows = [(c['display'], skey, s) for cname, c in CONCEPTS.items()
                for skey, s in c['series'].items() if s['source'] == src]
        if not rows:
            continue
        win = rows[0][2]['window']
        items = '；'.join('%s/%s=%s（分母：%s）' % (disp, skey, s['field'], s['denominator'])
                          for disp, skey, s in rows)
        lines.append('> - **%s**（%s，%s）：%s' % (src, SOURCE_DESC[src], win, items))
    lines.append('> 跨源/跨 series 比较前先对齐窗口与分母；同概念多 series 配对见「跨 series 对照」节。')
    return '\n'.join(lines)


def readme_section() -> str:
    """README『指标口径』节（按概念分组；改 CONCEPTS 后重新生成本节保持同步）。"""
    out = ['## 指标口径（CONCEPTS · 按概念分组）', '']
    for cname, c in CONCEPTS.items():
        out.append('#### %s（`%s`，单位：%s）' % (c['display'], cname, c['unit']))
        if c.get('note'):
            out.append('> ' + c['note'])
        out.append('')
        out.append('| series_key | 源字段 | 所属源 | 窗口 | 窗口天数 | 分母 | 备注 |')
        out.append('|---|---|---|---|---|---|---|')
        for skey, s in c['series'].items():
            out.append('| `%s` | `%s` | %s | %s | %s | %s | %s |' % (
                skey, s['field'], s['source'], s['window'], s['window_days'],
                s['denominator'], s.get('note', '')))
        out.append('')
    out.append('### 自动推导的易混淆配对（同一概念多 series）')
    out.append('')
    for cname, a, b, why in auto_pairs():
        ca, cb = CONCEPTS[cname]['series'][a], CONCEPTS[cname]['series'][b]
        out.append('- **%s**：`%s`(%s·%s·分母「%s」) vs `%s`(%s·%s·分母「%s」) — %s' % (
            cname, a, ca['source'], ca['window'], ca['denominator'],
            b, cb['source'], cb['window'], cb['denominator'], why))
    out.append('')
    out.append('> 本节由 `mpdt/metrics.py` CONCEPTS 自动生成（'
               '`python3 -c "from mpdt.metrics import readme_section; print(readme_section())"`）。'
               '改口径请改 CONCEPTS，再运行 `mpdt metrics` 校对。')
    return '\n'.join(out)
