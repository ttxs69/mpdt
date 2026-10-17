"""metrics（口径注册表）单元测试：注册表完整性 / lookup / to_dicts / 口径文档生成。
运行：python3 tests/test_metrics.py 或 unittest discover  （零第三方依赖）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import metrics                                    # noqa: E402


class TestRegistryIntegrity(unittest.TestCase):
    def test_every_metric_has_seven_attrs(self):
        for m in metrics.all_metrics():
            self.assertTrue(m.name and m.field and m.source, m)
            self.assertTrue(m.window and m.unit, m)
            self.assertIsInstance(m.comparable, bool, m)

    def test_sources_cover_all_three(self):
        got = {m.source for m in metrics.all_metrics()}
        self.assertEqual(got, set(metrics.SOURCES))

    def test_field_unique_within_source(self):
        seen = set()
        for m in metrics.all_metrics():
            key = (m.source, m.field)
            self.assertNotIn(key, seen, '同源内字段应唯一: %s' % (key,))
            seen.add(key)

    def test_core_fields_registered(self):
        # 三源入库时实际写的键必须全部登记（与 cli.py / fetchers 对齐）
        dc = {'refdate', 'finish_read_rate', 'read_rate', 'total_pv',
              'total_read_finish', 'total_share', 'send_succ'}
        pl = {'read_num', 'share_num', 'like_num', 'old_like_num', 'comment_num'}
        ad = set(__import__('mpdt.fetchers.article_detail', fromlist=['METRIC_KEYS']).METRIC_KEYS)
        ad |= {'publish_date', 'window_days', 'total_read_user', 'channel_breakdown'}
        reg = {(m.source, m.field) for m in metrics.all_metrics()}
        for src, keys in (('datacube_article', dc), ('publish_list', pl), ('article_detail', ad)):
            for k in keys:
                self.assertIn((src, k), reg, '未登记: %s.%s' % (src, k))

    def test_two_finish_read_rates_not_comparable(self):
        f7 = metrics.lookup('finish_read_rate', 'datacube_article')
        f30 = metrics.lookup('finished_read_pv_ratio', 'article_detail')
        self.assertEqual(f7.window, '发表后 7 天窗口，T+1 出数')
        self.assertEqual(f30.window, '发表后 30 天窗口')
        self.assertFalse(f7.comparable)
        self.assertFalse(f30.comparable)
        self.assertNotEqual(f7.denominator, f30.denominator)

    def test_cross_pairs_fields_registered(self):
        reg = {(m.source, m.field) for m in metrics.all_metrics()}
        for fa, sa, fb, sb, _ in metrics.CROSS_SOURCE_PAIRS:
            self.assertIn((sa, fa), reg)
            self.assertIn((sb, fb), reg)


class TestLookup(unittest.TestCase):
    def test_lookup_by_field_without_source(self):
        m = metrics.lookup('read_num')
        self.assertEqual(m.source, 'publish_list')

    def test_lookup_unknown_returns_none(self):
        self.assertIsNone(metrics.lookup('no_such_field'))

    def test_lookup_source_mismatch_returns_none(self):
        self.assertIsNone(metrics.lookup('read_num', 'article_detail'))

    def test_by_source(self):
        ms = metrics.by_source('datacube_article')
        self.assertTrue(ms)
        self.assertTrue(all(m.source == 'datacube_article' for m in ms))


class TestToDicts(unittest.TestCase):
    def test_full_list_shape_and_count(self):
        ds = metrics.to_dicts()
        self.assertEqual(len(ds), len(metrics.all_metrics()))
        for e in ds:
            self.assertEqual(set(e) >= {'name', 'field', 'source', 'window',
                                        'denominator', 'unit', 'comparable'}, True)

    def test_query_substring_filter(self):
        ds = metrics.to_dicts('完读率')
        fields = {e['field'] for e in ds}
        self.assertIn('finish_read_rate', fields)
        self.assertIn('finished_read_pv_ratio', fields)
        self.assertTrue(all('完读率' in e['name'] for e in ds))

    def test_query_no_match_empty(self):
        self.assertEqual(metrics.to_dicts('zzz不存在'), [])


class TestGeneratedDocs(unittest.TestCase):
    def test_calibration_note_from_registry(self):
        note = metrics.calibration_note()
        self.assertIn('口径说明', note)
        self.assertIn('publish_list', note)
        self.assertIn('datacube_article', note)
        self.assertIn('article_detail', note)
        self.assertIn('7 天窗口', note)
        self.assertIn('30 天窗口', note)
        self.assertIn('不可直接相比', note)

    def test_calibration_note_has_no_hardcode_drift(self):
        # 注册表里每个源的字段都应出现在口径说明里（生成而非硬编码的证明）
        note = metrics.calibration_note()
        for m in metrics.all_metrics():
            self.assertIn(m.field, note, m.field)

    def test_readme_section_table_and_pairs(self):
        sec = metrics.readme_section()
        self.assertTrue(sec.startswith('| 规范名 |'))
        self.assertEqual(sec.count('\n') >= len(metrics.all_metrics()), True)
        for fa, sa, fb, sb, _ in metrics.CROSS_SOURCE_PAIRS:
            self.assertIn('%s`(%s)' % (fa, sa), sec)
            self.assertIn('%s`(%s)' % (fb, sb), sec)
        self.assertIn('不可直接比', sec)


if __name__ == '__main__':
    unittest.main()
