"""next_step（R67/R68）—— 能力边界说明只在有可执行结论时出现。"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

from mpdt import next_step
from mpdt.next_step import render_next_step


def test_actionable_verdicts_render() -> None:
    for v in ("FAMILY_SPLIT", "FAMILY_SPARK", "BLACKOUT", "NO_SIGNAL"):
        out = render_next_step({"verdict": v, "headline": "x"})
        assert out, v
        for need in ("mpdt 到哪一步为止", "\u00a599", "\u00a5499", "ws.kevin@qq.com"):
            assert need in out, (v, need)


def test_insufficient_does_not_render() -> None:
    """样本不足时提「人工版」就是骗人 —— 必须不出现。"""
    for v in ("INSUFFICIENT", "NORMAL", "", None):
        assert render_next_step({"verdict": v, "headline": "x"}) == "", v


def test_invalid_input() -> None:
    # 运行时防御：调用方传进来的可能不是 dict（cron / 外部脚本）
    assert render_next_step(None) == ""
    assert render_next_step("x") == ""


def test_no_promises() -> None:
    out = render_next_step({"verdict": "FAMILY_SPLIT", "headline": "x"})
    for bad in (r"\u4fdd\u8bc1\s*\u6da8\u7c89", r"\u4e00\u5b9a\s*(?:\u80fd)?\u88ab\u63a8\u8350"):
        assert not re.search(bad, out), bad
    assert "\u4e0d\u627f\u8bfa\u9605\u8bfb\u91cf" in out


# 原来的 next_step.self_test() 已搬成下面三个 TestCase 类（模块因此保持纯）。


def test_actionable_verdict_without_reason_returns_empty() -> None:
    """防御分支：判定在 _ACTIONABLE 里但 _WHY_HUMAN 没写理由时，宁可不出这一节，
    也不能输出半截话。用清空字典造出这种不一致。"""
    with mock.patch.object(next_step, "_WHY_HUMAN", {}):
        assert render_next_step({"verdict": "FAMILY_SPLIT"}) == ""


class TestExecutableVerdicts(unittest.TestCase):
    """正例：有可执行结论 ⇒ 必须出现，且要素齐全。"""

    def test_every_executable_verdict_renders_all_elements(self) -> None:
        for v in ("FAMILY_SPLIT", "FAMILY_SPARK", "BLACKOUT", "NO_SIGNAL"):
            out = next_step.render_next_step({"verdict": v, "headline": "x"})
            self.assertTrue(out, f"可执行判定 {v} 没渲染下一步")
            for need in (
                "mpdt 到哪一步为止",
                "¥99",
                "¥499",
                "¥1,999",
                next_step._CONTACT,
                "不承诺阅读量",
            ):
                self.assertIn(need, out, f"{v} 缺要素: {need}")


class TestNonExecutableVerdicts(unittest.TestCase):
    """负例：样本不足 / 非法输入 ⇒ 不许出现（否则就是骗人）。"""

    def test_insufficient_and_invalid_render_nothing(self) -> None:
        for v in ("INSUFFICIENT", "NORMAL", "", None):
            self.assertFalse(
                next_step.render_next_step({"verdict": v, "headline": "x"}),
                f"非可执行判定 {v!r} 仍渲染了下一步",
            )

    def test_garbage_input_renders_nothing(self) -> None:
        self.assertFalse(next_step.render_next_step(None))
        self.assertFalse(next_step.render_next_step("x"))


class TestNoPromises(unittest.TestCase):
    """负例：不得出现"保证/一定涨粉"这类承诺。

    要区分肯定承诺与否定承诺 —— "我们**不**承诺阅读量"是允许的，用裸子串判会把
    自己写的不承诺也判成违规（第一版就踩了这个坑）。
    """

    def test_no_positive_promise_wording(self) -> None:
        out = next_step.render_next_step({"verdict": "FAMILY_SPLIT", "headline": "x"})
        for banned in (r"保证\s*涨粉", r"一定\s*(?:能)?被推荐", r"(?<!不)承诺\s*阅读量", r"包涨粉"):
            self.assertIsNone(re.search(banned, out), f"出现承诺性措辞: {banned}")
        self.assertIn("不承诺阅读量", out, "缺少明确的不承诺声明")
