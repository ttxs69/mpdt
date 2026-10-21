"""判断层（advice.py）的单元测试。

R240 教训：**只测"正常"不够，必须测"会误判的边界"**。
最典型的误判：按家族中位数判"这系列吃不到推荐"⇒ 停掉一个能爆的系列。
实测「rea 登顶」8 篇推荐分布 [60,1,0,0,0,0,0,0]，中位数 0 但有 60 的孤峰。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpdt import advice, analysis, store  # noqa: E402


def _mk_rows():
    """构造 4 个家族的样本，覆盖：主力型 / 孤峰型 / 全零型 / 小样本。"""
    rows = []
    # 主力型：10 篇，推荐 50±
    for i in range(10):
        rows.append({'date': '2026-10-%02d' % (10 + i % 5), 'title': 'arxiv AI-Agent 精选 %d' % i,
                     'read': 100, 'rec': 50 + i % 10, 'msg': 10, 'fin': 0.1, 'form': None})
    # 孤峰型：8 篇，其中 1 篇 60，其余 0
    for i in range(8):
        rows.append({'date': '2026-10-%02d' % (10 + i % 5), 'title': 'rea 登顶 GH Trending %d' % i,
                     'read': 30, 'rec': (60 if i == 0 else 0), 'msg': 8, 'fin': 0.1, 'form': None})
    # 全零型：3 篇全 0
    for i in range(3):
        rows.append({'date': '2026-10-%02d' % (10 + i % 3), 'title': 'claude 插件榜 %d' % i,
                     'read': 20, 'rec': 0, 'msg': 12, 'fin': 0.1, 'form': None})
    # 单篇（不参与家族判断）
    rows.append({'date': '2026-10-12', 'title': '孤例一篇', 'read': 5, 'rec': 0, 'msg': 1,
                 'fin': 0.1, 'form': None})
    return rows


class TestGroupRows(unittest.TestCase):
    def test_spike_detected_when_one_outlier_wins(self):
        fams = analysis.group_rows(_mk_rows())
        spark = [v for k, v in fams.items() if k.startswith('rea')][0]
        self.assertEqual(spark['n'], 8)
        self.assertEqual(spark['hit_n'], 1)
        self.assertEqual(spark['rec_max'], 60)
        self.assertEqual(spark['rec_med'], 0.0)   # 中位确实是 0
        self.assertTrue(spark['spike'])           # 但必须识别出孤峰

    def test_all_zero_family_is_not_spike(self):
        fams = analysis.group_rows(_mk_rows())
        zero = [v for k, v in fams.items() if k.startswith('claude')][0]
        self.assertEqual(zero['hit_n'], 0)
        self.assertFalse(zero['spike'])

    def test_main_family_is_not_spike(self):
        fams = analysis.group_rows(_mk_rows())
        main = [v for k, v in fams.items() if k.startswith('arxiv')][0]
        self.assertFalse(main['spike'])
        self.assertEqual(main['hit_n'], 10)

    def test_single_article_family_excluded(self):
        """单篇家族不进家族级判断。

        group_rows 保留原始分组（便于展示"样本不足"），
        过滤在 family_verdict 的 usable 里做。
        """
        fams = analysis.group_rows(_mk_rows())
        self.assertTrue(any('孤例' in k for k in fams))       # 原始分组里有
        fv = analysis.family_verdict(_mk_rows())
        self.assertFalse(any('孤例' in k for k in fv['usable']))  # 但判断时排除


class TestAdvise(unittest.TestCase):
    def test_spike_family_does_not_get_stopped(self):
        """核心回归：孤峰家族不能被建议"停掉"。"""
        adv = advice.advise(_mk_rows())
        text = ' '.join(adv.get('actions') or [])
        # 注意：'别停掉这一形态' 本身含 '停掉这一形态' 子串，
        # 所以不能直接 assertNotIn —— 判据是**不出现建议停发的那条**。
        self.assertIn('别停掉这一形态', text)
        self.assertNotIn('先停发或大改', text)

    def test_verdict_is_family_spark(self):
        adv = advice.advise(_mk_rows())
        self.assertEqual(adv['verdict'], 'FAMILY_SPARK')

    def test_actions_are_executable_not_vague(self):
        adv = advice.advise(_mk_rows())
        for a in adv['actions']:
            self.assertTrue(len(a) > 10, '动作过短，疑似空话：%r' % a)
            # 禁止"优化内容质量"这类正确但无用的话
            self.assertNotIn('优化内容质量', a)
            self.assertNotIn('提升质量', a)

    def test_all_zero_families_not_claimed_as_split(self):
        """所有家族中位都是 0 ⇒ 不能报「家族分化」（否则是拿 0 比 0）。"""
        rows = []
        for i in range(4):
            rows.append({'date': '2026-10-1%d' % i, 'title': 'aaa 系列 %d' % i,
                         'read': 20, 'rec': 0, 'msg': 5, 'fin': 0.1, 'form': None})
        for i in range(4):
            rows.append({'date': '2026-10-2%d' % i, 'title': 'bbb 系列 %d' % i,
                         'read': 20, 'rec': 0, 'msg': 5, 'fin': 0.1, 'form': None})
        fv = analysis.family_verdict(rows)
        self.assertNotEqual(fv['verdict'], 'FAMILY_SPLIT')

    def test_insufficient_data_does_not_judge(self):
        adv = advice.advise([{'date': '2026-10-20', 'title': '只有一篇', 'read': 10,
                              'rec': 5, 'msg': 1, 'fin': None, 'form': None}])
        self.assertEqual(adv['verdict'], 'INSUFFICIENT')
        self.assertEqual(len(adv['actions']), 1)

    def test_empty_rows(self):
        adv = advice.advise([])
        self.assertEqual(adv['verdict'], 'INSUFFICIENT')

    def test_blackout_takes_priority_over_family(self):
        """断供期优先：断供期的零推荐不是内容问题。"""
        rows = []
        for d in range(10, 16):
            for k in range(2):
                rows.append({'date': '2026-10-%02d' % d,
                             'title': 'arxiv 精选 %d-%d' % (d, k), 'read': 50,
                             'rec': 0, 'msg': 10, 'fin': 0.1, 'form': None})
        adv = advice.advise(rows)
        self.assertEqual(adv['verdict'], 'BLACKOUT')
        self.assertIn('断供', adv['headline'])


class TestBlackout(unittest.TestCase):
    def test_two_consecutive_low_days_form_window(self):
        rows = []
        for d in (10, 11, 12, 13):
            for k in range(2):
                rows.append({'date': '2026-10-%02d' % d, 'title': 'x %d-%d' % (d, k),
                             'read': 50, 'rec': (0 if d in (11, 12) else 40),
                             'msg': 5, 'fin': 0.1, 'form': None})
        b = advice.detect_blackout(rows)
        self.assertTrue(b['windows'])
        self.assertEqual(len(b['blackout_rows']), 4)   # 2 天 × 2 篇
        self.assertEqual(len(b['clean_rows']), 4)

    def test_single_day_not_a_window(self):
        rows = [{'date': '2026-10-11', 'title': 'a', 'read': 50, 'rec': 0,
                 'msg': 5, 'fin': 0.1, 'form': None},
                {'date': '2026-10-11', 'title': 'b', 'read': 50, 'rec': 1,
                 'msg': 5, 'fin': 0.1, 'form': None}]
        b = advice.detect_blackout(rows)
        self.assertFalse(b['windows'])


class TestRender(unittest.TestCase):
    def test_render_includes_verdict_and_actions(self):
        md = advice.render(advice.advise(_mk_rows()))
        self.assertIn('本周该做什么', md)
        self.assertIn('家族有孤峰', md)
        self.assertIn('具体动作', md)

    def test_render_includes_caveats(self):
        md = advice.render(advice.advise(_mk_rows()))
        self.assertIn('⚠', md)


class TestLoadRows(unittest.TestCase):
    def test_loads_latest_snapshot_per_article(self):
        """必须取每个文章**最新**的快照 —— 早期快照 read_uv=0 会误判"没量"。"""
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, 't.sqlite3'))
        store.upsert(con, 'article_detail', '2026-10-20', 'p1', 1, '早快照',
                     {'publish_date': '2026-10-19', 'read_uv': 0,
                      'channel_breakdown': [{'scene_label': '推荐', 'read_user': 0}]})
        store.upsert(con, 'article_detail', '2026-10-21', 'p1', 1, '晚快照',
                     {'publish_date': '2026-10-19', 'read_uv': 50,
                      'channel_breakdown': [{'scene_label': '推荐', 'read_user': 30}]})
        con.commit()
        rows = advice.load_rows(con)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['read'], 50)
        self.assertEqual(rows[0]['rec'], 30)


if __name__ == '__main__':
    unittest.main()
