"""report 单元测试（T2）：_week_bounds / _md_table / generate（含空数据周）/ 跨 series 宽表对照。

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
        for i, (am, fr) in enumerate([('mA', 0.10), ('mB', 0.40), ('mC', 0.25)]):
            store.upsert(self.con, 'datacube_article', '2026-10-1%d' % (2 + i), am, 1,
                         '文章' + am, {'refdate': '2026-10-1%d' % (2 + i),
                                        'finish_read_rate': fr, 'read_rate': 0.5,
                                        'total_pv': 100 + i, 'total_share': 3})
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mA', 1, '文章mA',
                     _detail_metrics(190, 0.25))
        store.upsert(self.con, 'article_detail', '2026-10-16', 'mB', 1, '文章mB',
                     _detail_metrics(500, 0.31, pub='2026-10-13'))
        # 同步 publish_list 也造两条，让 read_volume 的 pl vs dc7 配对有数据
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
    """跨 series 对照：每概念一张宽表（不再 C(N,2) 张两两对照）。"""

    def test_returns_one_block_per_multi_series_concept(self):
        blocks = report.cross_source_table(self.con)
        # 每概念至多一块（不再多张两两对照表）
        seen = {}
        for b in blocks:
            self.assertNotIn(b['concept'], seen, '%s 应只有一块' % b['concept'])
            seen[b['concept']] = b
        # 至少要含完读率/阅读量/分享/点赞/评论——listen 已拆走不再有配对
        concepts_seen = {b['concept'] for b in blocks}
        for c in ('read_completion', 'read_volume', 'share', 'like', 'comment'):
            self.assertIn(c, concepts_seen, '跨 series 对照应含概念 %s' % c)
        # listen 不再以「一对」出现（已拆 listen_people/listen_plays 各 1 series）
        self.assertNotIn('listen', concepts_seen)
        # metadata 概念也不在
        for meta in ('refdate', 'publish_date', 'window_days', 'total_read_user',
                     'channel_breakdown'):
            self.assertNotIn(meta, concepts_seen)

    def test_finish_read_wide_table_shape(self):
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        # 宽表：列 = 文章 / appmsgid_itemidx / dc7值 / ad30值 / 差值(两两差)
        self.assertIn('文章', blk['header'])
        self.assertIn('appmsgid_itemidx', blk['header'])
        self.assertIn('完读率(dc7·7天)', blk['header'])
        self.assertIn('完读率(ad30·30天)', blk['header'])
        self.assertIn('差值(百分点·两两差)', blk['header'])
        # fixture：mA 与 mB 两篇同时有 dc7 和 ad30
        self.assertEqual(len(blk['rows']), 2)
        by_key = {r[1]: r for r in blk['rows']}
        # mA：dc 0.10 vs ad 0.25 → 行末差值 -15.0
        self.assertEqual(by_key['mA_1'][2], '10.0%')
        self.assertEqual(by_key['mA_1'][3], '25.0%')
        self.assertIn('−', by_key['mA_1'][4])   # 差值列用全角负号
        self.assertIn('-15.0pt', by_key['mA_1'][4])
        # mB：dc 0.40 vs ad 0.31 → +9.0
        self.assertIn('+9.0pt', by_key['mB_1'][4])

    def test_read_volume_wide_table_not_percent(self):
        # 阅读量 wide 表：pl=80 vs dc7=100 不会有 % 符号
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_volume')
        self.assertTrue(blk['rows'])
        for r in blk['rows']:
            for cell in r[2:]:
                self.assertNotIn('%', cell)
        # 阅读量不是 ratio，不应有「差值(百分点·两两差)」列
        for h in blk['header']:
            self.assertNotIn('百分点', h)

    def test_note_warns_not_comparable(self):
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        self.assertIn('不可直接比', blk['note'])
        # series key 在 note 里；窗口/分母差异要点出
        self.assertIn('dc7', blk['note'])
        self.assertIn('ad30', blk['note'])
        self.assertIn('7 天', blk['note'])
        self.assertIn('30 天', blk['note'])

    def test_no_overlap_marks_has_data_false(self):
        con2 = store.connect(os.path.join(self._tmp.name, 'mem.sqlite3'))
        store.upsert(con2, 'datacube_article', '2026-10-15', 'mA', 1, 'a',
                     {'finish_read_rate': 0.1})
        store.upsert(con2, 'article_detail', '2026-10-16', 'mZ', 1, 'z',
                     {'finished_read_pv_ratio': 0.2})
        blocks = report.cross_source_table(con2)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        self.assertEqual(blk['rows'], [])
        self.assertFalse(blk['has_data'])
        con2.close()

    def test_latest_snapshot_wins_per_key(self):
        # mA 在 article_detail 有 10-16 与 10-17 两个快照 → 取 10-17（0.25）
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        by_key = {r[1]: r for r in blk['rows']}
        self.assertEqual(by_key['mA_1'][3], '25.0%')

    def test_missing_value_excluded_from_wide_row(self):
        # mC 在 article_detail 没有 finished_read_pv_ratio → 不进 read_completion 宽表
        store.upsert(self.con, 'article_detail', '2026-10-17', 'mC', 1, '文章mC',
                     {'read_uv': 10})
        self.con.commit()
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b['concept'] == 'read_completion')
        self.assertEqual(len(blk['rows']), 2)

    def test_concept_filter_returns_only_matching(self):
        blocks = report.cross_source_table(self.con, concept='read_completion')
        self.assertEqual({b['concept'] for b in blocks}, {'read_completion'})

    def test_no_hardcoded_finish_read_rate_only(self):
        # 回归保护：cross_source_table 必须泛化到 ≥2 个概念
        blocks = report.cross_source_table(self.con)
        self.assertGreaterEqual(len({b['concept'] for b in blocks}), 2)

    def test_one_block_per_concept_not_C_N_2(self):
        # 之前点赞概念 4 series → C(4,2)=6 张表；现在每概念只 1 张
        blocks = report.cross_source_table(self.con)
        like_blocks = [b for b in blocks if b['concept'] == 'like']
        self.assertEqual(len(like_blocks), 1, 'like 概念应只 1 张宽表（不再 6 张）')
        # like 宽表应只有 pl + ad30 两列数据（不出现 ad30z/zaikan_cnt）
        like = like_blocks[0]
        field_columns = [h for h in like['header'] if '·' in h]
        self.assertEqual(len(field_columns), 2, 'like 宽表只 2 个 series 列')
        for h in field_columns:
            self.assertNotIn('zaikan_cnt', h)
            self.assertNotIn('old_like_num', h)


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
        self.assertIn('口径说明', md)
        self.assertIn('按概念分组', md)
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

    def test_md_contains_wide_table_for_read_completion(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        self.assertIn('跨 series 对照', md)
        # 完读率宽表：列头 dc7 + ad30 都出现
        self.assertIn('完读率(dc7·7天)', md)
        self.assertIn('完读率(ad30·30天)', md)
        self.assertIn('mA_1', md)
        self.assertIn('10.0%', md)
        self.assertIn('25.0%', md)
        self.assertIn('不可直接比', md)
        # 阅读量宽表：3 列（pl / dc7 / ad30）
        self.assertIn('阅读量(pl·0天)', md)
        self.assertIn('阅读量(dc7·7天)', md)
        self.assertIn('阅读量(ad30·30天)', md)
        # 返回里声明了对照的概念
        self.assertIn('read_completion', out['concepts_compared'])

    def test_md_folds_empty_concepts_into_summary(self):
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        # 无数据概念折叠成一行（带"缺字段 X"诊断：fixture 没存 like_cnt/comment_cnt/share_uv）
        self.assertIn('本周无可对照数据的概念', md)
        # 缺字段 article_detail 字段：点赞/评论/分享
        self.assertIn('缺字段', md)
        self.assertIn('article_detail', md)
        # 折叠的概念名应该列出
        self.assertIn('点赞', md)
        # 折叠的概念列表在 concepts_empty 里
        self.assertIn('like', out['concepts_empty'])
        self.assertIn('comment', out['concepts_empty'])

    def test_all_zero_rows_folded_as_no_signal(self):
        # 整张宽表全 0 → 折叠成「无信号」汇总
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        # pl 全 0，ad30 全 0 → like 概念全 0
        store.upsert(con, 'publish_list', '2026-10-16', '1', 1, '甲',
                     {'like_num': 0, 'comment_num': 0})
        store.upsert(con, 'article_detail', '2026-10-17', '1', 1, '甲',
                     {'like_cnt': 0, 'comment_cnt': 0})
        con.commit()
        out = report.generate(con, '2026-W42', tmp)
        md = open(out['md']).read()
        # 全 0 的 like/comment 概念应被折叠到「无信号」汇总
        self.assertIn('无信号', md)
        # 不应再展示它们的宽表（避免噪音）
        self.assertNotIn('### 概念「点赞」', md)
        self.assertNotIn('### 概念「评论」', md)
        # 但 like/comment 仍记在 folded 里
        self.assertIn('like', out['concepts_empty'])
        self.assertIn('comment', out['concepts_empty'])

    def test_md_no_pairwise_block(self):
        # 回归保护：周报里不应再出现旧式「series `a` vs `b`」小节（每概念一张宽表）
        out = report.generate(self.con, '2026-W42', self.out_dir)
        md = open(out['md']).read()
        self.assertNotIn('series `pl` vs `dc7`', md)
        self.assertNotIn('series `pl` vs `ad30`', md)

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
        self.assertEqual(out['rows'], 3)
        md = open(out['md']).read()
        self.assertNotIn('mX', md)


class TestHeaderUnitSemantics(unittest.TestCase):
    """ratio 概念宽表=「差值(百分点·两两差)」列；计数概念宽表无差值列。"""

    def test_count_concept_header_has_no_diff_column(self):
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        store.upsert(con, 'publish_list', '2026-10-16', '999', 1, '甲',
                     {'read_num': 96}, fetched_at='2026-10-17T09:00:00')
        store.upsert(con, 'datacube_article', '2026-10-16', '999', 1, '甲',
                     {'total_pv': 208}, fetched_at='2026-10-17T09:00:00')
        con.commit()
        for t in report.cross_source_table(con, 'read_volume'):
            for h in t['header']:
                self.assertNotIn('百分点', h)
                self.assertNotIn('两两差', h)
        for t in report.cross_source_table(con, 'read_completion'):
            self.assertIn('差值(百分点·两两差)', t['header'])


class TestFoldingBehavior(unittest.TestCase):
    """空数据折叠：无数据行的概念合并成一行汇总。"""

    def test_no_data_concepts_listed_in_summary(self):
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        # 只造一篇 datacube，没有 article_detail / publish_list
        store.upsert(con, 'datacube_article', '2026-10-15', '1', 1, '甲',
                     {'finish_read_rate': 0.3, 'total_pv': 100})
        con.commit()
        out = report.generate(con, '2026-W42', tmp)
        md = open(out['md']).read()
        # 折叠汇总行
        self.assertIn('本周无可对照数据的概念', md)
        # 完读率也无数据（ad30 不存在）
        self.assertIn('完读率', md)
        # concepts_compared 为空
        self.assertEqual(out['concepts_compared'], [])
        # 但 concepts_empty 应含完读率
        self.assertIn('read_completion', out['concepts_empty'])

    def test_all_concepts_have_data_no_folding(self):
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        # 同时给 pl + dc7 → read_volume 完整
        store.upsert(con, 'publish_list', '2026-10-16', '1', 1, '甲',
                     {'read_num': 80, 'share_num': 1, 'like_num': 1,
                      'old_like_num': 1, 'comment_num': 0})
        store.upsert(con, 'datacube_article', '2026-10-15', '1', 1, '甲',
                     {'finish_read_rate': 0.3, 'total_pv': 100, 'total_share': 5,
                      'send_succ': 50, 'total_read_finish': 30, 'read_rate': 0.5,
                      'refdate': '2026-10-15'})
        store.upsert(con, 'article_detail', '2026-10-17', '1', 1, '甲',
                     {'read_uv': 200, 'finished_read_pv_ratio': 0.25,
                      'avg_article_read_time': 12, 'share_uv': 8, 'like_cnt': 3,
                      'comment_cnt': 1, 'collection_uv': 1, 'listen_uv': 5,
                      'listen_pv': 7, 'zaikan_cnt': 2, 'praise_money': 0,
                      'follow_after_read_uv': 0, 'publish_date': '2026-10-15'})
        con.commit()
        out = report.generate(con, '2026-W42', tmp)
        md = open(out['md']).read()
        # 所有 ≥2 series 概念都有数据
        for c in ('read_completion', 'read_volume', 'share', 'like', 'comment'):
            self.assertIn(c, out['concepts_compared'])
        # 折叠行不应出现（或为空）
        # 当所有多 series 概念都有数据时 concepts_empty 为空
        self.assertEqual(out['concepts_empty'], [])


if __name__ == '__main__':
    unittest.main()


class TestTopTablePercentFormat(unittest.TestCase):
    """Top 表比例列（完读率/阅读率）必须是百分号格式，与宽表/渠道表一致。"""

    def test_top_ratio_columns_formatted(self):
        import tempfile, os
        from mpdt import store, report as rep
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        store.upsert(con, 'datacube_article', '2026-10-13', '777', 1, '甲文',
                     {'finish_read_rate': 0.3684, 'read_rate': 0.0653,
                      'total_pv': 25, 'total_share': 1}, fetched_at='2026-10-14T09:00:00')
        con.commit()
        out = rep.generate(con, '2026-W42', os.path.join(tmp, 'r'))
        md = open(out['md']).read()
        self.assertIn('36.8%', md)
        self.assertIn('6.5%', md)
        self.assertNotIn('0.3684', md)
