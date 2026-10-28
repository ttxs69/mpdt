"""判断层（advice.py）的单元测试。

R240 教训：**只测"正常"不够，必须测"会误判的边界"**。
最典型的误判：按家族中位数判"这系列吃不到推荐"⇒ 停掉一个能爆的系列。
实测「rea 登顶」8 篇推荐分布 [60,1,0,0,0,0,0,0]，中位数 0 但有 60 的孤峰。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpdt import advice, analysis, store


def _mk_rows() -> Any:
    """构造 4 个家族的样本，覆盖：主力型 / 孤峰型 / 全零型 / 小样本。"""
    rows = []
    # 主力型：10 篇，推荐 50±
    for i in range(10):
        rows.append(
            {
                "date": f"2026-10-{10 + i % 5:02}",
                "title": f"arxiv AI-Agent 精选 {i}",
                "read": 100,
                "rec": 50 + i % 10,
                "msg": 10,
                "fin": 0.1,
                "form": None,
            }
        )
    # 孤峰型：8 篇，其中 1 篇 60，其余 0
    for i in range(8):
        rows.append(
            {
                "date": f"2026-10-{10 + i % 5:02}",
                "title": f"rea 登顶 GH Trending {i}",
                "read": 30,
                "rec": (60 if i == 0 else 0),
                "msg": 8,
                "fin": 0.1,
                "form": None,
            }
        )
    # 全零型：3 篇全 0
    for i in range(3):
        rows.append(
            {
                "date": f"2026-10-{10 + i % 3:02}",
                "title": f"claude 插件榜 {i}",
                "read": 20,
                "rec": 0,
                "msg": 12,
                "fin": 0.1,
                "form": None,
            }
        )
    # 单篇（不参与家族判断）
    rows.append(
        {
            "date": "2026-10-12",
            "title": "孤例一篇",
            "read": 5,
            "rec": 0,
            "msg": 1,
            "fin": 0.1,
            "form": None,
        }
    )
    return rows


class TestGroupRows(unittest.TestCase):
    def test_spike_detected_when_one_outlier_wins(self) -> None:
        fams = analysis.group_rows(_mk_rows())
        spark = next(v for k, v in fams.items() if k.startswith("rea"))
        self.assertEqual(spark["n"], 8)
        self.assertEqual(spark["hit_n"], 1)
        self.assertEqual(spark["rec_max"], 60)
        self.assertEqual(spark["rec_med"], 0.0)  # 中位确实是 0
        self.assertTrue(spark["spike"])  # 但必须识别出孤峰

    def test_all_zero_family_is_not_spike(self) -> None:
        fams = analysis.group_rows(_mk_rows())
        zero = next(v for k, v in fams.items() if k.startswith("claude"))
        self.assertEqual(zero["hit_n"], 0)
        self.assertFalse(zero["spike"])

    def test_main_family_is_not_spike(self) -> None:
        fams = analysis.group_rows(_mk_rows())
        main = next(v for k, v in fams.items() if k.startswith("arxiv"))
        self.assertFalse(main["spike"])
        self.assertEqual(main["hit_n"], 10)

    def test_single_article_family_excluded(self) -> None:
        """单篇家族不进家族级判断。

        group_rows 保留原始分组（便于展示"样本不足"），
        过滤在 family_verdict 的 usable 里做。
        """
        fams = analysis.group_rows(_mk_rows())
        self.assertTrue(any("孤例" in k for k in fams))  # 原始分组里有
        fv = analysis.family_verdict(_mk_rows())
        self.assertFalse(any("孤例" in k for k in fv["usable"]))  # 但判断时排除


class TestAdvise(unittest.TestCase):
    def test_spike_family_does_not_get_stopped(self) -> None:
        """核心回归：孤峰家族不能被建议"停掉"。"""
        adv = advice.advise(_mk_rows())
        text = " ".join(adv.get("actions") or [])
        # 注意：'别停掉这一形态' 本身含 '停掉这一形态' 子串，
        # 所以不能直接 assertNotIn —— 判据是**不出现建议停发的那条**。
        self.assertIn("别停掉这一形态", text)
        self.assertNotIn("先停发或大改", text)

    def test_verdict_is_family_spark(self) -> None:
        adv = advice.advise(_mk_rows())
        self.assertEqual(adv["verdict"], "FAMILY_SPARK")

    def test_actions_are_executable_not_vague(self) -> None:
        adv = advice.advise(_mk_rows())
        for a in adv["actions"]:
            self.assertTrue(len(a) > 10, f"动作过短，疑似空话：{a!r}")
            # 禁止"优化内容质量"这类正确但无用的话
            self.assertNotIn("优化内容质量", a)
            self.assertNotIn("提升质量", a)

    def test_all_zero_families_not_claimed_as_split(self) -> None:
        """所有家族中位都是 0 ⇒ 不能报「家族分化」（否则是拿 0 比 0）。"""
        rows = []
        for i in range(4):
            rows.append(
                {
                    "date": f"2026-10-1{i}",
                    "title": f"aaa 系列 {i}",
                    "read": 20,
                    "rec": 0,
                    "msg": 5,
                    "fin": 0.1,
                    "form": None,
                }
            )
        for i in range(4):
            rows.append(
                {
                    "date": f"2026-10-2{i}",
                    "title": f"bbb 系列 {i}",
                    "read": 20,
                    "rec": 0,
                    "msg": 5,
                    "fin": 0.1,
                    "form": None,
                }
            )
        fv = analysis.family_verdict(rows)
        self.assertNotEqual(fv["verdict"], "FAMILY_SPLIT")

    def test_insufficient_data_does_not_judge(self) -> None:
        adv = advice.advise(
            [
                {
                    "date": "2026-10-20",
                    "title": "只有一篇",
                    "read": 10,
                    "rec": 5,
                    "msg": 1,
                    "fin": None,
                    "form": None,
                }
            ]
        )
        self.assertEqual(adv["verdict"], "INSUFFICIENT")
        self.assertEqual(len(adv["actions"]), 1)

    def test_empty_rows(self) -> None:
        adv = advice.advise([])
        self.assertEqual(adv["verdict"], "INSUFFICIENT")

    def test_blackout_takes_priority_over_family(self) -> None:
        """断供期优先：断供期的零推荐不是内容问题。"""
        rows = []
        for d in range(10, 16):
            for k in range(2):
                rows.append(
                    {
                        "date": f"2026-10-{d:02}",
                        "title": f"arxiv 精选 {d}-{k}",
                        "read": 50,
                        "rec": 0,
                        "msg": 10,
                        "fin": 0.1,
                        "form": None,
                    }
                )
        adv = advice.advise(rows)
        self.assertEqual(adv["verdict"], "BLACKOUT")
        self.assertIn("断供", adv["headline"])


class TestBlackout(unittest.TestCase):
    def test_two_consecutive_low_days_form_window(self) -> None:
        rows = []
        for d in (10, 11, 12, 13):
            for k in range(2):
                rows.append(
                    {
                        "date": f"2026-10-{d:02}",
                        "title": f"x {d}-{k}",
                        "read": 50,
                        "rec": (0 if d in (11, 12) else 40),
                        "msg": 5,
                        "fin": 0.1,
                        "form": None,
                    }
                )
        b = advice.detect_blackout(rows)
        self.assertTrue(b["windows"])
        self.assertEqual(len(b["blackout_rows"]), 4)  # 2 天 × 2 篇
        self.assertEqual(len(b["clean_rows"]), 4)

    def test_single_day_not_a_window(self) -> None:
        rows = [
            {
                "date": "2026-10-11",
                "title": "a",
                "read": 50,
                "rec": 0,
                "msg": 5,
                "fin": 0.1,
                "form": None,
            },
            {
                "date": "2026-10-11",
                "title": "b",
                "read": 50,
                "rec": 1,
                "msg": 5,
                "fin": 0.1,
                "form": None,
            },
        ]
        b = advice.detect_blackout(rows)
        self.assertFalse(b["windows"])


class TestRender(unittest.TestCase):
    def test_render_includes_verdict_and_actions(self) -> None:
        md = advice.render(advice.advise(_mk_rows()))
        self.assertIn("本周该做什么", md)
        self.assertIn("家族有孤峰", md)
        self.assertIn("具体动作", md)

    def test_render_includes_caveats(self) -> None:
        md = advice.render(advice.advise(_mk_rows()))
        self.assertIn("⚠", md)


class TestLoadRows(unittest.TestCase):
    def test_loads_latest_snapshot_per_article(self) -> None:
        """必须取每个文章**最新**的快照 —— 早期快照 read_uv=0 会误判"没量"。"""
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        store.upsert(
            con,
            "article_detail",
            "2026-10-20",
            "p1",
            1,
            "早快照",
            {
                "publish_date": "2026-10-19",
                "read_uv": 0,
                "channel_breakdown": [{"scene_label": "推荐", "read_user": 0}],
            },
        )
        store.upsert(
            con,
            "article_detail",
            "2026-10-21",
            "p1",
            1,
            "晚快照",
            {
                "publish_date": "2026-10-19",
                "read_uv": 50,
                "channel_breakdown": [{"scene_label": "推荐", "read_user": 30}],
            },
        )
        con.commit()
        rows = advice.load_rows(con)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["read"], 50)
        self.assertEqual(rows[0]["rec"], 30)


class FamilySplitRegressionTests(unittest.TestCase):
    """FAMILY_SPLIT 分支回归（R68）。

    背景：这条分支的 headline 用 7 个参数填 6 个占位符，走到就抛
    `TypeError: not all arguments converted during string formatting`。
    而原有测试只断言 verdict != "FAMILY_SPLIT" —— **刻意绕开了这条分支**，
    所以 232 个测试全绿也没发现。质量门（ruff F507）第一次跑就抓到了。

    这里钉住三件事：
      1) 能走到 FAMILY_SPLIT（构造出 ratio>=2 且低分组无孤峰的数据）
      2) 不抛异常（占位符与参数个数必须一致）
      3) headline 里的数字真的被填进去了（不是空串或未格式化）
    """

    @staticmethod
    def _rows() -> list[dict[str, object]]:
        # 主力家族：2 篇，推荐中位 40（>= hit_rec=20）
        # 低分家族：2 篇，推荐全 0 ⇒ 无 hit ⇒ spike=False ⇒ 走 FAMILY_SPLIT 而不是 SPARK
        return [
            {"title": "alpha 复盘 1", "rec": 40, "read": 100},
            {"title": "alpha 复盘 2", "rec": 40, "read": 100},
            {"title": "beta 笔记 1", "rec": 0, "read": 5},
            {"title": "beta 笔记 2", "rec": 0, "read": 5},
        ]

    def test_family_split_does_not_crash_and_formats(self) -> None:
        fv = analysis.family_verdict(self._rows())
        self.assertEqual(fv["verdict"], "FAMILY_SPLIT")

        headline = fv["headline"]
        self.assertIn("倍", headline)
        self.assertIn("alpha 复盘", headline)
        self.assertIn("beta 笔记", headline)
        # 占位符必须被真实数字替换掉，不能留下未格式化的 %s/%d
        for leftover in ("%s", "%d", "%.1f"):
            self.assertNotIn(leftover, headline)

    def test_family_split_actions_are_concrete(self) -> None:
        fv = analysis.family_verdict(self._rows())
        self.assertGreaterEqual(len(fv["actions"]), 2)
        joined = "\n".join(fv["actions"])
        self.assertIn("alpha 复盘", joined)
        self.assertIn("beta 笔记", joined)
        # 排名/停发建议必须带具体篇数，否则客户无法复算
        self.assertRegex(joined, r"\d+ 篇")

    def test_title_family_strips_separator_tail(self) -> None:
        """标题带分隔符时，家族名必须切到分隔符之前（否则同一系列会散成多个家族）。"""
        self.assertEqual(analysis.title_family("alpha 复盘 · 10月19日"), "alpha 复盘")
        self.assertEqual(analysis.title_family("beta 笔记：第二期"), "beta 笔记")
        self.assertEqual(analysis.title_family("gamma 系列 | 番外"), "gamma 系列")

    def test_title_family_empty_and_no_title(self) -> None:
        self.assertEqual(analysis.title_family(""), "(无标题)")
        self.assertEqual(analysis.title_family("   "), "(无标题)")


if __name__ == "__main__":
    unittest.main()
