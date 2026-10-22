"""分析内核（纯 stdlib，无数据库依赖）：内容家族分组 + 孤峰判定。

为什么独立成模块（R240）：
- mpdt 自抓数据路径（report.py → advice.py）和客户导出路径（diagnose.py）
  **必须用同一套判断规则**，否则同一个号在两条路上会得到矛盾结论，
  服务就没有可信度。
- 这个模块不碰数据库、不碰 mpdt 私有状态 ⇒ 可被 diagnose.py 直接 import。

核心洞见（来自真实数据，2026-10-21）：
「rea 登顶 GH Trending」8 篇的推荐阅读分布是 [60,1,0,0,0,0,0,0] ——
中位数 0，但有 60 的孤峰。
**按中位数判「这形态吃不到推荐」⇒ 会停掉一个能爆的系列。**
所以分组结论必须区分：
  - 组内无孤峰  ⇒ 形态不吃推荐（停发有依据）
  - 组内有孤峰  ⇒ 形态偶尔能爆（不能停，去找可复制变量）
"""

from __future__ import annotations

import re
import statistics

# 命中阈值：推荐渠道阅读人数 ≥ 此值算「吃到推荐」。
# **唯一常量源**（R241）：mpdt 自抓路径（advice.py）和客户导出路径（diagnose.py）
# 必须用同一个值，否则同一个号在两条路上会得到矛盾结论，服务就没有可信度。
# 取 20 = 与 diagnose.py 的「进推荐池」阈值一致（推荐池是分发的二元判定，不是连续刻度）。
HIT_REC = 20
# 孤峰判定：命中篇数 ≤ 组内篇数/4 且 最高值 ≥ HIT_REC
SPIKE_FRACTION = 0.25


def _strip_tail(t: str) -> str:
    """剥掉标题尾部的日期/序号。

    必须做这一步（2026-10-21 实测 bug）：
    'claude 插件榜 0' / '1' / '2' 若不剥尾号，会各自成家族（n=1）
    ⇒ 同系列被拆散 ⇒ 家族级结论全错。
    """
    t = re.sub(
        r"[（(]?\s*(?:\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2})\s*[）)]?\s*$", "", t
    )
    t = re.sub(r"[（(]?\s*\d+\s*[）)]?\s*$", "", t)
    return t.strip(" 　-—·|")


def title_family(title: str) -> str:
    """从标题推断「内容家族」。

    客户数据没有 form 标签，只能靠标题前缀归类。
    实测有效：
      'ai-job-search 登顶 GH Trending · 10月19日' → 'ai-job-search 登顶'
      'claude 插件榜 0' / '1' / '2'                → 'claude 插件榜'
      'arxiv AI-Agent 每日精选 · 2026年10月20日'   → 'arxiv AI-Agent 每'
    """
    t = (title or "").strip()
    # **不要把 '-' 当分隔符**（R241 实测 bug）：'arxiv AI-Agent 每日精选' 会被切成
    # 'arxiv AI'，同一系列在两条路径下变成两个家族名。
    for sep in ("·", "｜", "|", "—", ":", "："):
        if sep in t:
            t = t.split(sep)[0].strip()
    t = t.split("  ")[0].strip()
    t = _strip_tail(t)
    return t[:18] if t else "(无标题)"


def group_rows(rows, hit_rec: int = HIT_REC) -> dict:
    """按内容家族分组，统计组内分布，并识别「孤峰」。

    返回 {family: {n, rec_med, rec_max, hit_n, hit_rate, spike, spike_max, spike_titles, read_med}}
    - spike: 该组内只有少数篇吃到推荐，但最高值够高 ⇒ 形态能爆，不是形态不吃推荐
    """
    fam = {}
    for r in rows:
        fam.setdefault(title_family(r.get("title")), []).append(r)

    out = {}
    for name, rs in fam.items():
        recs = [(r.get("rec") or 0) for r in rs]
        hit = [r for r in rs if (r.get("rec") or 0) >= hit_rec]
        n = len(rs)
        spike = bool(hit) and len(hit) <= max(1, n * SPIKE_FRACTION) and max(recs) >= hit_rec
        spike_rows = sorted(hit, key=lambda r: -(r.get("rec") or 0)) if spike else []
        reads = [r.get("read") or 0 for r in rs]
        out[name] = {
            "n": n,
            "rec_med": statistics.median(recs) if recs else 0.0,
            "rec_max": max(recs) if recs else 0,
            "hit_n": len(hit),
            "hit_rate": len(hit) / n if n else 0.0,
            "spike": spike,
            "spike_max": spike_rows[0].get("rec") or 0 if spike_rows else 0,
            "spike_titles": [str(r.get("title") or "") for r in spike_rows],
            "read_med": statistics.median(reads) if reads else 0.0,
        }
    return out


def family_verdict(rows, hit_rec: int = HIT_REC, min_n: int = 2) -> dict:
    """家族级判定：返回 verdict + 可执行动作。

    verdict:
      NONE              —— 没有足够样本做家族判断
      FAMILY_SPLIT     —— 主力 vs 停发（有据）
      FAMILY_SPARK     —— 低分家族里有孤峰 ⇒ 不能停，要找可复制变量
    """
    base_caveat = "家族按标题前缀归类，同名系列会合并；样本 <%d 篇的家族不参与" % min_n
    fams = group_rows(rows, hit_rec=hit_rec)
    usable = {k: v for k, v in fams.items() if v["n"] >= min_n}
    if len(usable) < 2:
        return {"verdict": "NONE", "families": fams, "usable": usable, "actions": [], "caveats": []}

    hi = max(usable.items(), key=lambda kv: kv[1]["rec_med"])
    # 选 lo 时**必须排除 hi 自己**：多个家族中位都是 0 时，min 可能选中 hi
    # （当 hi 也是 0 时 ratio 恒为 1，永远不触发判定）。
    lo_cands = {k: v for k, v in usable.items() if k != hi[0]}
    # R68：这里原本还有个 `if not lo_cands: return NONE（只有一个家族）` 分支。
    # 它**不可达**：函数开头已挡掉 len(usable) < 2，而家族名是 dict 的键（唯一），
    # 所以 usable 里至少 2 个不同键 ⇒ 去掉 hi[0] 后至少剩 1 个。
    # 不可达分支会让覆盖率永远差一截，也会让读代码的人以为存在"只剩一家"的状态。
    lo = min(lo_cands.items(), key=lambda kv: kv[1]["rec_med"])
    ratio = (hi[1]["rec_med"] + 1) / (lo[1]["rec_med"] + 1)

    if ratio >= 2.0 and hi[1]["rec_med"] >= hit_rec:
        if lo[1]["spike"]:
            titles = "；".join((t[:40] + "…" if len(t) > 40 else t) for t in lo[1]["spike_titles"])
            return {
                "verdict": "FAMILY_SPARK",
                "hi": hi[0],
                "lo": lo[0],
                "ratio": ratio,
                "families": fams,
                "usable": usable,
                "headline": (
                    '「%s」不是"完全吃不到推荐"——%d 篇里有 %d 篇吃到（最高 %d 推荐阅读），'
                    "其余为 0。这是**内容事件**的功劳，不是形态的功劳。"
                    % (lo[0], lo[1]["n"], lo[1]["hit_n"], lo[1]["spike_max"])
                ),
                "actions": [
                    "**别停掉这一形态**——它能爆（最高 %d 推荐阅读），只是不常爆。"
                    % lo[1]["spike_max"],
                    "把爆过的那 %d 篇找出来对比（标题/选题/发布时机），找可复制的变量。%s"
                    % (lo[1]["hit_n"], ("已知爆款：" + titles) if titles else ""),
                    "「%s」是主力（%d 篇，推荐中位 %d）——排期给它留位置。"
                    % (hi[0], hi[1]["n"], hi[1]["rec_med"]),
                    "「%s」保持现状但降低频率（%d 篇里只有 %d 篇吃到推荐，中位 %d）。"
                    % (lo[0], lo[1]["n"], lo[1]["hit_n"], lo[1]["rec_med"]),
                ],
                "caveats": [
                    base_caveat,
                    "「孤峰」判定：该组命中篇数 ≤ 组内篇数/4 且 max ≥ %d" % hit_rec,
                ],
            }
        return {
            "verdict": "FAMILY_SPLIT",
            "hi": hi[0],
            "lo": lo[0],
            "ratio": ratio,
            "families": fams,
            "usable": usable,
            "headline": (
                "两类内容吃推荐的差距 %.1f 倍：「%s」推荐阅读中位 %d，"
                "「%s」只有 %d（%d 篇全部低于阈值，无孤峰）。"
                % (ratio, hi[0], hi[1]["rec_med"], lo[0], lo[1]["rec_med"], lo[1]["n"])
            ),
            "actions": [
                "把「%s」当主力（%d 篇，推荐阅读中位 %d）——下周的排期给它留位置。"
                % (hi[0], hi[1]["n"], hi[1]["rec_med"]),
                "「%s」先停发或大改（%d 篇，推荐阅读中位 %d，0 篇吃到推荐）。"
                % (lo[0], lo[1]["n"], lo[1]["rec_med"]),
                f"别在这两类之间平均用力——这 {ratio:.1f} 倍差距是分发侧的偏好，不是内容质量的连续刻度。",
            ],
            "caveats": [base_caveat, "只有「组内无孤峰」才判停发；有孤峰走 FAMILY_SPARK"],
        }

    return {
        "verdict": "NONE",
        "families": fams,
        "usable": usable,
        "headline": "家族之间没有 2 倍以上的推荐差异，暂不判优劣。",
        "actions": [],
        "caveats": [base_caveat],
    }
