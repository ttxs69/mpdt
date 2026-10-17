"""report 单元测试（T2）：_week_bounds / _md_table / generate（含空数据周）/ 跨 series 对照。

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
        # 故意让 article_detail 的 read_uv 与 datacube 的 total_pv 也共 appmsgid，
        # 以便跨 series 对照能同时覆盖「完读率」与「阅读量」两个概念
        for i, (am, fr) in enumerate([('mA', 0.10), ('mB', 0.40), ('mC', 0.25)]):
            store.upsert(self.con, 'datacube_article', '2026-10-1%d' % (2 + i), am, 1,
                         '文章' + am, {'refdate': '2026-10-1%d' % (2 + i),
                                        'finish_read_rate': fr, 'read_rate': 0.5,
                                        'total_pv': 100 + i, 'total_share': 3})
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mA', 1, '文章mA',
                     _detail_metrics(190, 0.25))
        store.upsert(self.con, 'article_detail', '2026-10-16', 'mB', 1, '文章mB',
                     _detail_metrics(500, 0.31, pub='2026-10-13'))
        # 同步 publish_list 也造两条，确保「阅读量」概念 pl vs dc7 配对也能生成表格
        store.upsert(self.con, 'publish_list', '2026-10-17', 'mA', 1, '文章mA',
                     {'read_num': 80, 'share_num': 5, 'like_num': 1,
                      'old_like_num': 1, 'comment_num': 0})
        store.upsert(self.con, 'publish_list', '2026-10-16', 'mB', 1, '文章mB',
                     {'read_num': 220, 'share_num': 8, 'like_num': 2,
                      'old_like_num': 1, 'comment_num': 1})
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()


class TestCrossSourceTable(ReportBase):
    """跨 series 对照：泛化到任意 ≥2 series 概念；返回 list of {concept, header, rows, note}。"""

    def test_returns_block_per_pair(self):
        blocks = report.cross_source_table(self.con)
        # 至少要含 完读率 + 阅读量 + 分享 + 点赞 + 评论 + 听读 六个概念的配对
        concepts_seen = {b['concept'] for b in blocks}
        for c in ('read_completion', 'read_volume', 'share', 'like', 'comment', 'listen'):
            self.assertIn(c, concepts_seen, '跨 series 对照应含概念 %s' % c)

    def test_finish_read_pair_matched_with_window_and_delta(self):
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        # fixture：mA 与 mB 两篇同时有 dc7 和 ad30
        self.assertEqual(len(blk['rows']), 2)
        self.assertIn('appmsgid_itemidx', blk['header'])
        by_key = {r[1]: r for r in blk['rows']}
        # mA：dc 0.10 vs ad 0.25 → -15.0 百分点
        self.assertEqual(by_key['mA_1'][2], '10.0%')
        self.assertEqual(by_key['mA_1'][3], '25.0%')
        self.assertEqual(by_key['mA_1'][4], '-15.0')
        self.assertIn('7 天窗口', by_key['mA_1'][5])
        self.assertIn('30 天窗口', by_key['mA_1'][5])
        # mB：dc 0.40 vs ad 0.31 → +9.0
        self.assertEqual(by_key['mB_1'][4], '+9.0')

    def test_read_volume_pair_uses_count_format_not_percent(self):
        # 阅读量 (total_pv=100 vs read_uv=190) 不应被误格式化为百分比
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_volume'
                   and set(b['pair']) == {'dc7', 'ad30'})
        self.assertTrue(blk['rows'])
        for r in blk['rows']:
            self.assertNotIn('%', r[2])
            self.assertNotIn('%', r[3])

    def test_note_warns_not_comparable(self):
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        self.assertIn('不可直接比', blk['note'])
        self.assertIn('finish_read_rate', blk['note'])
        self.assertIn('finished_read_pv_ratio', blk['note'])
        self.assertIn('7 天窗口', blk['note'])
        self.assertIn('30 天窗口', blk['note'])

    def test_no_overlap_gives_empty_rows(self):
        con2 = store.connect(os.path.join(self._tmp.name, 'mem.sqlite3'))
        store.upsert(con2, 'datacube_article', '2026-10-15', 'mA', 1, 'a',
                     {'finish_read_rate': 0.1})
        store.upsert(con2, 'article_detail', '2026-10-16', 'mZ', 1, 'z',
                     {'finished_read_pv_ratio': 0.2})
        blocks = report.cross_source_table(con2)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        self.assertEqual(blk['rows'], [])
        con2.close()

    def test_latest_snapshot_wins_per_key(self):
        # mA 在 article_detail 有 10-16 与 10-17 两个快照 → 取 10-17（0.25）
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        by_key = {r[1]: r for r in blk['rows']}
        self.assertEqual(by_key['mA_1'][3], '25.0%')

    def test_missing_ratio_value_skipped(self):
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mC', 1, '文章mC',
                     {'read_uv': 10})        # 无 finished_read_pv_ratio
        self.con.commit()
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        # mC 不进对照表
        self.assertEqual(len(blk['rows']), 2)

    def test_concept_filter_returns_only_matching(self):
        # concept 参数只返回该概念
        blocks = report.cross_source_table(self.con, concept='read_completion')
        self.assertEqual({b['concept'] for b in blocks}, {'read_completion'})

    def test_no_hardcoded_finish_read_rate_only(self):
        # 回归保护：cross_source_table 不能再只对照完读率一对
        blocks = report.cross_source_table(self.con)
        self.assertGreaterEqual(len({b['concept'] for b in blocks}), 2,
                                'cross_source_table 必须泛化到 ≥2 个概念')


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
        # 文首口径说明（R4，由 CONCEPTS 生成）与周区间
        self.assertIn('口径说明', md)
        self.assertIn('mpdt/metrics.py 概念树生成', md)
        self.assertIn('2026-10-12 ~ 2026-10-18', md)
        # Top10 按完读率降序：mB(0.40) > mC(0.25) > mA(0.10)
        self.assertLess(md.index('文章mB'), md.index('文章mC'))
        self.assertLess(md.index('文章mC'), md.index('文章mA'))
        # 单篇渠道构成段
        self.assertIn('单篇渠道构成', md)
        self.assertIn('45.0', md)
        self.assertIn('25.0%', md)
        html = open(out['html']).read()
        self.assertIn('<pre>', html)
        self.assertIn('口径说明', html)

    def test_md_contains_cross_series_section(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        self.assertIn('跨 series 对照', md)
        # 至少出现 2 个概念
        self.assertIn('概念「完读率」', md)
        self.assertIn('概念「阅读量」', md)
        self.assertIn('mA_1', md)
        self.assertIn('-15.0', md)
        self.assertIn('不可直接比', md)
        self.assertGreaterEqual(out['cross_rows'], 3)  # 完读率 2 行 + 阅读量 2 行 + 其它
        # 返回里也声明了对照的概念
        self.assertIn('read_completion', out['concepts_compared'])
        self.assertIn('read_volume', out['concepts_compared'])

    def test_md_cross_section_empty_without_overlap(self):
        con2 = store.connect(os.path.join(self._tmp.name, 'mem.sqlite3'))
        store.upsert(con2, 'datacube_article', '2026-10-15', 'mA', 1, 'a',
                     {'finish_read_rate': 0.1})
        out = report.generate(con2, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        # 无交集时各概念块给出"无对照行"说明
        self.assertIn('无跨 series 可对照行', md)
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


class TestHeaderUnitSemantics(unittest.TestCase):
    """ratio 概念表头=差值(百分点)，计数概念表头=差值（不带百分点）。"""

    def test_count_concept_header_has_no_points(self):
        import tempfile, os
        from mpdt import store, report as rep
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        store.upsert(con, 'publish_list', '2026-10-16', '999', 1, '甲',
                     {'read_num': 96}, fetched_at='2026-10-17T09:00:00')
        store.upsert(con, 'datacube_article', '2026-10-16', '999', 1, '甲',
                     {'total_pv': 208}, fetched_at='2026-10-17T09:00:00')
        con.commit()
        for t in rep.cross_source_table(con, 'read_volume'):
            self.assertNotIn('百分点', t['header'][4])
        for t in rep.cross_source_table(con, 'read_completion'):
            self.assertIn('百分点', t['header'][4])
