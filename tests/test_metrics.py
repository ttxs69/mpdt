"""metrics（概念分类树）单元测试：CONCEPTS 完整性 / series 查找 / 自动配对推导 / 文档生成。

运行：python3 tests/test_metrics.py 或 unittest discover  （零第三方依赖）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import metrics                                    # noqa: E402


class TestConceptTreeIntegrity(unittest.TestCase):
    """CONCEPTS 结构完整性：每个概念必有 display/unit/series；每个 series 6 字段必填。"""

    def test_every_concept_has_required_shape(self):
        for cname, c in metrics.CONCEPTS.items():
            self.assertTrue(cname and c['display'] and c.get('unit'), cname)
            self.assertIn('series', c)
            self.assertTrue(c['series'], '%s 至少一个 series' % cname)
            for skey, s in c['series'].items():
                for k in ('source', 'field', 'window', 'window_days', 'denominator'):
                    self.assertIn(k, s, '%s.%s 缺 %s' % (cname, skey, k))
                self.assertIn(s['source'], metrics.SOURCES,
                              '%s.%s 源 %s 不在 SOURCES' % (cname, skey, s['source']))

    def test_sources_cover_all_three(self):
        got = {s['source'] for _, _, s in metrics.all_series()}
        self.assertEqual(got, set(metrics.SOURCES))

    def test_field_unique_within_source(self):
        seen = {}
        for _, _, s in metrics.all_series():
            key = (s['source'], s['field'])
            self.assertNotIn(key, seen, '同源内 field 应唯一: %s (另有 %s)' % (key, seen.get(key)))
            seen[key] = s

    def test_window_days_consistent_with_source(self):
        # window_days 是 series 身份的一部分；pl=0, dc=7, ad=30
        for _, _, s in metrics.all_series():
            wd = s['window_days']
            if s['source'] == 'publish_list':
                self.assertEqual(wd, 0)
            elif s['source'] == 'datacube_article':
                self.assertEqual(wd, 7)
            elif s['source'] == 'article_detail':
                self.assertEqual(wd, 30)

    def test_core_fields_registered(self):
        # 三源入库时实际写的键必须全部登记（与 fetchers 对齐）
        dc = {'refdate', 'finish_read_rate', 'read_rate', 'total_pv',
              'total_read_finish', 'total_share', 'send_succ'}
        pl = {'read_num', 'share_num', 'like_num', 'old_like_num', 'comment_num'}
        ad = set(__import__('mpdt.fetchers.article_detail', fromlist=['METRIC_KEYS']).METRIC_KEYS)
        ad |= {'publish_date', 'window_days', 'total_read_user', 'channel_breakdown'}
        reg = {(s['source'], s['field']) for _, _, s in metrics.all_series()}
        for src, keys in (('datacube_article', dc), ('publish_list', pl), ('article_detail', ad)):
            for k in keys:
                self.assertIn((src, k), reg, '未登记: %s.%s' % (src, k))

    def test_two_finish_read_rates_belong_to_same_concept(self):
        a = metrics.find_series(field='finish_read_rate')
        b = metrics.find_series(field='finished_read_pv_ratio')
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        # 同概念
        self.assertEqual(a[0], b[0])
        # 窗口不同、分母不同
        self.assertNotEqual(a[2]['window'], b[2]['window'])
        self.assertNotEqual(a[2]['denominator'], b[2]['denominator'])


class TestFindSeries(unittest.TestCase):
    def test_by_field_without_source(self):
        hit = metrics.find_series(field='read_num')
        self.assertIsNotNone(hit)
        cname, skey, s = hit
        self.assertEqual(s['source'], 'publish_list')
        self.assertEqual(cname, 'read_volume')

    def test_unknown_returns_none(self):
        self.assertIsNone(metrics.find_series(field='no_such_field'))

    def test_with_source_disambiguates(self):
        # 完读率同概念下两个 series：限定 source 应各命中一条
        a = metrics.find_series(field='finish_read_rate', source='datacube_article')
        b = metrics.find_series(field='finished_read_pv_ratio', source='article_detail')
        self.assertEqual(a[1], 'dc7')
        self.assertEqual(b[1], 'ad30')

    def test_source_mismatch_returns_none(self):
        # 字段不与指定源匹配
        self.assertIsNone(metrics.find_series(field='read_num', source='article_detail'))


class TestToConceptsView(unittest.TestCase):
    def test_full_view_shape_and_counts(self):
        view = metrics.to_concepts_view()
        self.assertEqual(len(view), len(metrics.CONCEPTS))
        for c in view:
            self.assertIn('concept', c)
            self.assertIn('display', c)
            self.assertIn('series', c)
            for s in c['series']:
                self.assertIn('key', s)
                self.assertIn('source', s)
                self.assertIn('field', s)
                self.assertIn('window', s)
                self.assertIn('window_days', s)
                self.assertIn('denominator', s)
        total_series = sum(len(c['series']) for c in view)
        self.assertEqual(total_series, sum(1 for _ in metrics.all_series()))

    def test_query_finds_concept_by_concept_name(self):
        view = metrics.to_concepts_view('完读率')
        concepts = {c['concept'] for c in view}
        self.assertIn('read_completion', concepts)
        for c in view:
            for s in c['series']:
                self.assertTrue('完读率' in c['display'] or '完读率' in s.get('note', ''))

    def test_query_no_match_empty(self):
        self.assertEqual(metrics.to_concepts_view('zzz不存在'), [])


class TestAutoPairs(unittest.TestCase):
    """易混淆配对自动推导：同概念 ≥2 series 即一对；why 由 window/denominator 差异生成。"""

    def test_at_least_one_pair_per_multi_series_concept(self):
        pairs = metrics.auto_pairs()
        concepts_with_pair = {p[0] for p in pairs}
        for cname, c in metrics.CONCEPTS.items():
            if len(c['series']) >= 2:
                self.assertIn(cname, concepts_with_pair,
                              '概念 %s 有 %d series 但未生成配对' % (cname, len(c['series'])))

    def test_no_pair_for_single_series_concept(self):
        pairs = metrics.auto_pairs()
        for cname, _, _, _ in pairs:
            self.assertGreaterEqual(len(metrics.CONCEPTS[cname]['series']), 2)

    def test_known_pair_finish_read_in_output(self):
        pairs = metrics.auto_pairs()
        # 完读率 (dc7, ad30) 必出现
        self.assertTrue(any(p[0] == 'read_completion' and p[1] == 'dc7' and p[2] == 'ad30'
                            for p in pairs))

    def test_why_mentions_window_or_denominator_diff(self):
        # 完读率配对的 why 必须提及窗口/分母差异
        for cname, a, b, why in metrics.auto_pairs():
            if cname == 'read_completion':
                self.assertIn('窗口', why)
                self.assertIn('分母', why)
                self.assertIn('不可直接比', why)

    def test_count_consistent_with_concept_combinations(self):
        # 配对数 = sum C(len(series), 2) for each multi-series concept
        from math import comb
        expected = sum(comb(len(c['series']), 2) for c in metrics.CONCEPTS.values()
                       if len(c['series']) >= 2)
        self.assertEqual(len(metrics.auto_pairs()), expected)


class TestGeneratedDocs(unittest.TestCase):
    def test_calibration_note_lists_all_three_sources(self):
        note = metrics.calibration_note()
        self.assertIn('口径说明', note)
        for src in metrics.SOURCES:
            self.assertIn(src, note)
        # 窗口 / 分母 / 同概念提示都要在
        self.assertIn('7 天窗口', note)
        self.assertIn('30 天窗口', note)
        self.assertIn('不可直接比', note)

    def test_calibration_note_contains_every_registered_field(self):
        # 生成而非硬编码的证明：每个 series 的 field 都应出现在口径说明里
        note = metrics.calibration_note()
        for _, _, s in metrics.all_series():
            self.assertIn(s['field'], note, s['field'])

    def test_readme_section_grouped_by_concept(self):
        sec = metrics.readme_section()
        # 整节以 ## 标题开头（H2 层级）
        self.assertTrue(sec.startswith('## 指标口径'), sec[:50])
        # 概念标题 + 系列表格 + 自动配对
        for cname, c in metrics.CONCEPTS.items():
            self.assertIn('#### %s' % c['display'], sec)
            for skey in c['series']:
                self.assertIn('`%s`' % skey, sec)
        self.assertIn('自动推导的易混淆配对', sec)
        # 至少 1 个配对出现在文档里
        pairs = metrics.auto_pairs()
        self.assertTrue(pairs)
        for cname, a, b, _ in pairs:
            self.assertIn(cname, sec)


if __name__ == '__main__':
    unittest.main()
