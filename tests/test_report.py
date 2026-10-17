"""report 单元测试（T2）：_week_bounds / _md_table / generate（含空数据周）。
运行：python3 tests/test_report.py 或 unittest discover  （零第三方依赖）
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import report                                  # noqa: E402
from mpdt import store                                   # noqa: E402


def _detail_metrics(read_uv, fr, pub='2026-10-12', rec_pct=45.0, msg_pct=30.0, srch_pct=8.0):
    """造 article_detail 快照 metrics（与 cmd_detail 入库字段对齐）。"""
    return {'read_uv': read_uv, 'finished_read_pv_ratio': fr, 'avg_article_read_time': 12,
            'publish_date': pub,
            'channel_breakdown': [
                {'scene': 6, 'scene_label': '推荐', 'read_user': 10, 'read_pct': rec_pct},
                {'scene': 0, 'scene_label': '公众号消息', 'read_user': 7, 'read_pct': msg_pct},
                {'scene': 7, 'scene_label': '搜一搜', 'read_user': 2, 'read_pct': srch_pct},
            ]}


class TestWeekBounds(unittest.TestCase):
    def test_midyear_week(self):
        monday, sunday = report._week_bounds('2026-W42')
        self.assertEqual(monday.isoformat(), '2026-10-12')
        self.assertEqual(sunday.isoformat(), '2026-10-18')

    def test_year_boundary_into_next_year(self):
        # 2026-W01 的周一是 2025-12-29：跨年周必须用 ISO 日历而非 1 月 1 日对齐
        monday, sunday = report._week_bounds('2026-W01')
        self.assertEqual(monday.isoformat(), '2025-12-29')
        self.assertEqual(sunday.isoformat(), '2026-01-04')

    def test_week53_crosses_year_end(self):
        monday, sunday = report._week_bounds('2020-W53')
        self.assertEqual(monday.isoformat(), '2020-12-28')
        self.assertEqual(sunday.isoformat(), '2021-01-03')

    def test_dayspan_is_seven(self):
        monday, sunday = report._week_bounds('2024-W09')
        self.assertEqual((sunday - monday).days, 6)


class TestMdTable(unittest.TestCase):
    def test_header_separator_and_rows(self):
        md = report._md_table(['文章', '完读率'], [['甲', 0.25], ['乙', 0.5]])
        lines = md.splitlines()
        self.assertEqual(lines[0], '| 文章 | 完读率 |')
        self.assertEqual(lines[1], '|---|---|')
        self.assertEqual(lines[2], '| 甲 | 0.25 |')
        self.assertEqual(lines[3], '| 乙 | 0.5 |')

    def test_empty_rows_only_header(self):
        md = report._md_table(['a'], [])
        self.assertEqual(md.splitlines(), ['| a |', '|---|'])


class ReportBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.con = store.connect(os.path.join(self._tmp.name, 'db.sqlite3'))
        self.out_dir = os.path.join(self._tmp.name, 'reports')
        # fixture：W42（2026-10-12 ~ 10-18）内三篇 datacube 文章 + 两篇 article_detail
        for i, (am, fr) in enumerate([('mA', 0.10), ('mB', 0.40), ('mC', 0.25)]):
            store.upsert(self.con, 'datacube_article', '2026-10-1%d' % (2 + i), am, 1,
                         '文章' + am, {'refdate': '2026-10-1%d' % (2 + i),
                                        'finish_read_rate': fr, 'read_rate': 0.5,
                                        'total_pv': 100 + i, 'total_share': 3})
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mA', 1, '文章mA',
                     _detail_metrics(190, 0.25))
        store.upsert(self.con, 'article_detail', '2026-10-16', 'mB', 1, '文章mB',
                     _detail_metrics(500, 0.31, pub='2026-10-13'))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()


class TestCrossSourceTable(ReportBase):
    def test_pairs_matched_with_window_and_delta(self):
        hdr, rows, note = report.cross_source_table(self.con)
        # fixture：mA 与 mB 两篇在两源都有 → 2 行
        self.assertEqual(len(rows), 2)
        self.assertIn('appmsgid_itemidx', hdr)
        by_key = {r[1]: r for r in rows}
        # mA：dc 0.10 vs ad 0.25 → -15.0 百分点
        self.assertEqual(by_key['mA_1'][2], '10.0%')
        self.assertEqual(by_key['mA_1'][3], '25.0%')
        self.assertEqual(by_key['mA_1'][4], '-15.0')
        self.assertIn('7 天窗口', by_key['mA_1'][5])
        self.assertIn('30 天窗口', by_key['mA_1'][5])
        # mB：dc 0.40 vs ad 0.31 → +9.0
        self.assertEqual(by_key['mB_1'][4], '+9.0')

    def test_note_warns_not_comparable(self):
        _, _, note = report.cross_source_table(self.con)
        self.assertIn('不可直接比', note)
        self.assertIn('finish_read_rate', note)
        self.assertIn('finished_read_pv_ratio', note)
        self.assertIn('7 天窗口', note)
        self.assertIn('30 天窗口', note)

    def test_no_overlap_gives_empty_rows(self):
        con2 = store.connect(os.path.join(self._tmp.name, 'mem.sqlite3'))
        store.upsert(con2, 'datacube_article', '2026-10-15', 'mA', 1, 'a',
                     {'finish_read_rate': 0.1})
        store.upsert(con2, 'article_detail', '2026-10-16', 'mZ', 1, 'z',
                     {'finished_read_pv_ratio': 0.2})
        hdr, rows, note = report.cross_source_table(con2)
        self.assertEqual(rows, [])
        con2.close()

    def test_latest_snapshot_wins_per_key(self):
        # mA 在 article_detail 有 10-16 与 10-17 两个快照 → 取 10-17（0.25）
        hdr, rows, note = report.cross_source_table(self.con)
        by_key = {r[1]: r for r in rows}
        self.assertEqual(by_key['mA_1'][3], '25.0%')

    def test_missing_ratio_value_skipped(self):
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mC', 1, '文章mC',
                     {'read_uv': 10})        # 无 finished_read_pv_ratio
        self.con.commit()
        hdr, rows, note = report.cross_source_table(self.con)
        self.assertEqual(len(rows), 2)       # mC 不进对照表


class TestGenerate(ReportBase):
    def test_returns_paths_and_rowcount(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        self.assertTrue(out['md'].endswith('weekly-2026-W42.md'))
        self.assertTrue(out['html'].endswith('weekly-2026-W42.html'))
        self.assertEqual(out['rows'], 3)
        self.assertTrue(os.path.exists(out['md']) and os.path.exists(out['html']))

    def test_md_content_calibration_and_top_ordering(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        # 文首口径说明（R4，由注册表生成）与周区间
        self.assertIn('口径说明', md)
        self.assertIn('mpdt/metrics.py 注册表生成', md)
        self.assertIn('2026-10-12 ~ 2026-10-18', md)
        # Top10 按完读率降序：mB(0.40) > mC(0.25) > mA(0.10)
        self.assertLess(md.index('文章mB'), md.index('文章mC'))
        self.assertLess(md.index('文章mC'), md.index('文章mA'))
        # 单篇渠道构成段：推荐/公众号消息/搜一搜三列有读数
        self.assertIn('单篇渠道构成', md)
        self.assertIn('45.0', md)   # 推荐%
        self.assertIn('25.0%', md)  # 完读率 0.25 → 25.0%
        # HTML 转义包裹正文
        html = open(out['html']).read()
        self.assertIn('<pre>', html)
        self.assertIn('口径说明', html)

    def test_md_contains_cross_source_section(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        self.assertIn('跨源对照', md)
        self.assertIn('mA_1', md)
        self.assertIn('-15.0', md)              # dc 0.10 - ad 0.25
        self.assertIn('不可直接比', md)
        self.assertEqual(out['cross_rows'], 2)

    def test_md_cross_section_empty_without_overlap(self):
        # 只留无交集数据 → 提示无对照文章而非报错
        con2 = store.connect(os.path.join(self._tmp.name, 'mem.sqlite3'))
        store.upsert(con2, 'datacube_article', '2026-10-15', 'mA', 1, 'a',
                     {'finish_read_rate': 0.1})
        out = report.generate(con2, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        self.assertIn('无跨源可对照文章', md)
        self.assertEqual(out['cross_rows'], 0)
        con2.close()

    def test_empty_week_still_generates_report(self):
        out = report.generate(self.con, '2026-W20', self.out_dir)
        self.assertEqual(out['rows'], 0)
        md = open(out['md']).read()
        self.assertIn('本周无 datacube 快照数据', md)
        self.assertIn('口径说明', md)

    def test_outside_week_rows_excluded(self):
        store.upsert(self.con, 'datacube_article', '2026-10-19', 'mX', 1, '周外文章',
                     {'finish_read_rate': 0.99, 'total_pv': 1})
        self.con.commit()
        out = report.generate(self.con, '2026-W42', self.out_dir)
        self.assertEqual(out['rows'], 3)              # 10-19 属于 W43，不计入
        md = open(out['md']).read()
        self.assertNotIn('mX', md)


if __name__ == '__main__':
    unittest.main()
