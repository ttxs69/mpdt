"""next_step（R67/R68）—— 能力边界说明只在有可执行结论时出现。"""

import os
import re
import sys

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
    assert render_next_step(None) == ""  # type: ignore[arg-type]
    assert render_next_step("x") == ""  # type: ignore[arg-type]


def test_no_promises() -> None:
    out = render_next_step({"verdict": "FAMILY_SPLIT", "headline": "x"})
    for bad in (r"\u4fdd\u8bc1\s*\u6da8\u7c89", r"\u4e00\u5b9a\s*(?:\u80fd)?\u88ab\u63a8\u8350"):
        assert not re.search(bad, out), bad
    assert "\u4e0d\u627f\u8bfa\u9605\u8bfb\u91cf" in out


def test_self_test_passes() -> None:
    """self_test 是给 CI / 人工调用的自检入口，它自己必须也是绿的。"""
    assert next_step.self_test() == 0


def test_actionable_verdict_without_reason_returns_empty() -> None:
    """防御分支：判定在 _ACTIONABLE 里但 _WHY_HUMAN 没写理由时，宁可不出这一节，
    也不能输出半截话。用清空字典造出这种不一致。"""
    with mock.patch.object(next_step, "_WHY_HUMAN", {}):
        assert render_next_step({"verdict": "FAMILY_SPLIT"}) == ""
