# -*- coding: utf-8 -*-
"""next_step（R67）—— 能力边界说明只在有可执行结论时出现。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mpdt.next_step import render_next_step


def test_actionable_verdicts_render():
    for v in ("FAMILY_SPLIT", "FAMILY_SPARK", "BLACKOUT", "NO_SIGNAL"):
        out = render_next_step({"verdict": v, "headline": "x"})
        assert out, v
        for need in ("mpdt 到哪一步为止", "\u00a599", "\u00a5499", "ws.kevin@qq.com"):
            assert need in out, (v, need)


def test_insufficient_does_not_render():
    """样本不足时提「人工版」就是骗人 —— 必须不出现。"""
    for v in ("INSUFFICIENT", "NORMAL", "", None):
        assert render_next_step({"verdict": v, "headline": "x"}) == "", v


def test_invalid_input():
    assert render_next_step(None) == ""
    assert render_next_step("x") == ""


def test_no_promises():
    out = render_next_step({"verdict": "FAMILY_SPLIT", "headline": "x"})
    import re
    for bad in (r"\u4fdd\u8bc1\s*\u6da8\u7c89", r"\u4e00\u5b9a\s*(?:\u80fd)?\u88ab\u63a8\u8350"):
        assert not re.search(bad, out), bad
    assert "\u4e0d\u627f\u8bfa\u9605\u8bfb\u91cf" in out


if __name__ == "__main__":
    import unittest
    unittest.main()
