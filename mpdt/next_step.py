#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下一步（next step）渲染器 —— 把"工具到此为止，人接手从哪开始"写进周报。

**为什么存在（R67）**
实测（2026-10-22）：mpdt 14 天 546 次克隆 / 133 个唯一克隆者，但 ¥99/¥499/¥1999
三档 0 成交。诊断：**流量在"用"侧，不在"问"侧**。
`mpdt report` 跑完就结束，终端里从没出现过"人工版"三个字 ⇒
133 个已经在用的人，根本不知道有人能接着做。

⇒ 把"下一步"写进周报本身：不是硬广，是**说明工具的能力边界在哪**，
让用完工具的人知道剩下的活归谁。

**原则（R62）**
- 只在**有真实结论**时出现（INSUFFICIENT / 无快照 ⇒ 不出现，那是"我也不知道"）
- 必须说清"工具做到了什么 / 没做到什么"，不许承诺阅读量、涨粉、能不能被推荐
- 每条给的都是**判断**，不是让客户照做的步骤（那不是商品）

**用法**
    from next_step import render_next_step
    md = render_next_step(advice_dict)
"""
from __future__ import annotations

import re

# 只有这些判定才值得提"人工版"——样本不足时提了就是骗人
_ACTIONABLE = {"FAMILY_SPLIT", "FAMILY_SPARK", "BLACKOUT", "NO_SIGNAL"}

# 判定 → 这一类问题为什么"机器给不了最终答案"
_WHY_HUMAN = {
    "FAMILY_SPLIT": (
        "同一个系列的篇目之间分发表现分裂了。"
        "工具能告诉你「哪篇是异类、异在哪」，"
        "但「这个号接下来该把哪一条线砍掉」是取舍判断，取决于你的目标和你手上的资源。"
    ),
    "FAMILY_SPARK": (
        "系列里出了一篇孤峰。"
        "工具能定位到「是这一篇拿到推荐」，"
        "但「这究竟是因为选题、标题、还是分发时机」需要看那篇本身和它前后的差异。"
    ),
    "BLACKOUT": (
        "检测到推荐断供窗。"
        "工具能算出断在哪几天、影响了哪几篇，"
        "但「要不要因此改内容节奏」取决于你的发布计划和外部因素。"
    ),
    "NO_SIGNAL": (
        "最近几篇基本没拿到推荐。"
        "工具能告诉你「确实没信号」以及每篇的绝对量，"
        "但「下一篇该写什么」是选题，本来就不该由数据工具替你决定。"
    ),
}

_TIERS = (
    ("¥99", "首次限时咨询（30 分钟）", "看完报告指出盲点，给 3 条针对你这个号的判断。不交报告，只交判断。同一号仅限一次。"),
    ("¥499", "单号诊断（24 小时）", "完整 7 节报告 + 逐篇定位 + 不要做什么清单 + 3 轮追问。30 天内升级月度，这 ¥499 全额抵扣。"),
    ("¥1,999/月", "月度陪跑", "每月 1 次诊断 + 4 次问答 + 月度趋势对比（单次诊断看不出系列是在变好还是变差，只有这项能看）。"),
)

_CONTACT = "ws.kevin@qq.com"


def render_next_step(adv: dict) -> str:
    """返回周报里的「下一步」Markdown 节；不适用时返回空串。"""
    if not isinstance(adv, dict):
        return ""
    verdict = (adv.get("verdict") or "").strip()
    if verdict not in _ACTIONABLE:
        # 样本不足 / 分发正常 ⇒ 这时候提"人工版"就是骗人，不出现
        return ""
    why = _WHY_HUMAN.get(verdict)
    if not why:
        return ""

    lines = ["", "## 下一步", ""]
    lines.append("**这一节说明 mpdt 到哪一步为止。**")
    lines.append("")
    lines.append(why)
    lines.append("")
    lines.append("工具能给的都在上面了，每个数字都带分母、能用自己后台复算。"
                 "剩下的需要有人看一眼你这个号本身——所以有这几档：")
    lines.append("")
    for price, name, desc in _TIERS:
        lines.append("- **%s · %s** —— %s" % (price, name, desc))
    lines.append("")
    lines.append("代运营/外包服务商：¥149/号，5 号起 ¥745，报告可白标转给你的客户。")
    lines.append("")
    lines.append("**我们不承诺阅读量、涨粉数、能不能被推荐**——平台分发不受单方控制，"
                 "承诺这些就是骗你。我们承诺：每个数字都带分母，你能自己复算。")
    lines.append("")
    lines.append("要人工版：写信到 <%s>，主题写「诊断」，说明你的号大概多少粉、"
                 "发了多少篇、现在最想解决的一个问题。24 小时内回，"
                 "回信里会先问你 1-2 个问题再给判断。" % _CONTACT)
    lines.append("")
    lines.append("> 不想留邮箱也行：<https://github.com/ttxs69/mpdt/issues> 在 GitHub 问问题 / 要人工版。")
    lines.append("")
    return "\n".join(lines)


def self_test() -> int:
    """自测：判定门 + 内容要素 + 负对照。"""
    fails = []

    # 正例：有可执行结论 ⇒ 必须出现
    for v in ("FAMILY_SPLIT", "FAMILY_SPARK", "BLACKOUT", "NO_SIGNAL"):
        out = render_next_step({"verdict": v, "headline": "x"})
        if not out:
            fails.append("可执行判定 %s 没渲染下一步" % v)
            continue
        for need in ("mpdt 到哪一步为止", "¥99", "¥499", "¥1,999", _CONTACT, "不承诺阅读量"):
            if need not in out:
                fails.append("%s 缺要素: %s" % (v, need))

    # 负例 1：样本不足 ⇒ 不许出现（否则就是骗人）
    for v in ("INSUFFICIENT", "NORMAL", "", None):
        if render_next_step({"verdict": v, "headline": "x"}):
            fails.append("非可执行判定 %r 仍渲染了下一步" % v)

    # 负例 2：非 dict
    if render_next_step(None) or render_next_step("x"):
        fails.append("非法输入仍渲染了下一步")

    # 负例 3：不得出现"保证/一定涨粉"这类承诺
    # 注意：要区分肯定承诺与否定承诺——"我们**不**承诺阅读量"是允许的，
    # 用裸子串判会把自己写的不承诺也判成违规（第一版就踩了这个坑）。
    out = render_next_step({"verdict": "FAMILY_SPLIT", "headline": "x"})
    for banned in (r"保证\s*涨粉", r"一定\s*(?:能)?被推荐", r"(?<!不)承诺\s*阅读量", r"包涨粉"):
        if re.search(banned, out):
            fails.append("出现承诺性措辞: %s" % banned)
    if "不承诺阅读量" not in out:
        fails.append("缺少明确的不承诺声明")

    if fails:
        print("❌ next_step 自测失败:")
        for f in fails:
            print("   -", f)
        return 1
    print("✅ next_step 自测通过（4 类可执行判定渲染 / 样本不足与非法输入不渲染 / 无承诺措辞）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(self_test())
