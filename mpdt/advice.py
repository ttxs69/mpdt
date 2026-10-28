"""周报「判断层」：从渠道数据推出**本周该做什么**。

为什么单独一个模块：
- `report.py` 是仪表盘——把数字摆出来。
- 判断层是决策——告诉客户"这周你该改什么"。**后者才是 ¥1,999 诊断里"分析"的重量**。
  只给数字不给判断，等于把客户自己的活儿还给他。

设计原则（都是 R229/R237/R238 踩出来的教训）：
1. **先看断供，再谈结构**。分发中断期的零推荐不是内容问题；
   把它和正常期池化算命中率，会得出"抽签"这种错归因（AP78 同族）。
2. **组间差异优先于账号级结论**。两组命中率差 ≥2 倍时，
   "你号的结构有问题"是没有信息量的结论，正确结论是"你的两类内容差 4 倍"。
3. **数据不足就说不**。n < 3 的分组不下结论；本周 < 2 篇不发建议。
4. **每条建议必须可执行**——指出「哪一类/哪一篇、做什么动作」，
   不给"优化内容质量"这种正确但无用的话。
5. **只用可回算的数**。所有引用的数字都能从同一份快照复算出来。

上游依赖：`diagnose.py` 的断供检测与分组逻辑（在 ~/moneymaker/offers/gzh-diagnosis）。
本模块不重复实现那套逻辑，只负责把 mpdt 的 channel_breakdown 转成 diagnose 的行格式，
再把结论翻译成动作。
"""

from __future__ import annotations

import json
import statistics

from . import analysis

# 断供阈值（与 diagnose.py 一致：某日全部文章推荐阅读最大值 < 3 且当日 ≥2 篇）
BLACKOUT_MAX_REC = 3.0
BLACKOUT_MIN_DAYS = 2

# 推荐阅读人数算「吃到推荐」的门槛
HIT_REC = analysis.HIT_REC  # 唯一常量源：analysis.py（R241）


def _channel_map(metrics: dict) -> dict:
    return {c.get("scene_label"): c for c in (metrics.get("channel_breakdown") or [])}


def load_rows(con, only_dates=None) -> list:
    """从 mpdt 快照构造诊断行。

    取每个 (appmsgid, itemidx) 的**最新** stat_date 快照 ——
    早期快照在新文章还没数据时是 read_uv=0，取早的会把好文章误判成"没量"。
    （R238 实测：10-19/10-20 的 5 篇取早快照全是 0，取最新是 21/56/92/103。）
    """
    latest = {}
    for sd, ti, m, am, ix in con.execute(
        "SELECT stat_date, title, metrics, appmsgid, itemidx FROM snapshots "
        "WHERE source='article_detail' ORDER BY stat_date"
    ):
        latest[(str(am), int(ix))] = (sd, ti, m)
    rows = []
    for (_am, _ix), (_sd, ti, m) in latest.items():
        d = json.loads(m)
        uv = d.get("read_uv")
        if not isinstance(uv, (int, float)):
            continue
        ch = _channel_map(d)
        rec = (ch.get("推荐") or {}).get("read_user")
        msg = (ch.get("公众号消息") or {}).get("read_user")
        fin = d.get("finished_read_pv_ratio")
        pd = d.get("publish_date")
        if only_dates and pd not in only_dates:
            continue
        rows.append(
            {
                "date": pd,
                "title": ti or "",
                "read": uv,
                "rec": rec if isinstance(rec, (int, float)) else 0,
                "msg": msg if isinstance(msg, (int, float)) else 0,
                "fin": fin if isinstance(fin, (int, float)) else None,
                "form": None,
            }
        )
    rows.sort(key=lambda r: (r["date"] or "", -r["rec"]))
    return rows


def detect_blackout(rows: list) -> dict:
    """推荐断供窗：逐日取「当日推荐最大值」，连续 ≥2 天都 < 3 → 一个断供窗。

    与 diagnose.py 同逻辑，但在本模块内独立实现，避免跨目录 import 依赖
    （mpdt 是要独立发布的 PyPI 包，不能反向依赖 ~/moneymaker）。
    """
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(float(r.get("rec") or 0))
    days = sorted(d for d in by_day if d)
    black_days = [d for d in days if len(by_day[d]) >= 2 and max(by_day[d]) < BLACKOUT_MAX_REC]
    windows, cur = [], []
    for d in days:
        if d in black_days:
            cur.append(d)
        else:
            if len(cur) >= BLACKOUT_MIN_DAYS:
                windows.append({"from": cur[0], "to": cur[-1], "days": len(cur)})
            cur = []
    if len(cur) >= BLACKOUT_MIN_DAYS:
        windows.append({"from": cur[0], "to": cur[-1], "days": len(cur)})
    in_win = {d for w in windows for d in black_days if w["from"] <= d <= w["to"]}
    return {
        "windows": windows,
        "black_days": sorted(in_win),
        "clean_rows": [r for r in rows if r["date"] not in in_win],
        "blackout_rows": [r for r in rows if r["date"] in in_win],
    }


def advise(rows: list, recent_days: int = 3) -> dict:
    """产出「本周该做什么」。

    返回 {verdict, headline, actions[], evidence{}, caveats[]}。
    verdict 取值：INSUFFICIENT / BLACKOUT / FAMILY_SPLIT / NORMAL / NO_SIGNAL
    """
    caveats = []
    if not rows:
        return {
            "verdict": "INSUFFICIENT",
            "headline": "本周没有可用于判断的数据。",
            "actions": [],
            "evidence": {},
            "caveats": ["无快照"],
        }

    b = detect_blackout(rows)
    clean = b["clean_rows"] or rows
    in_black = bool(b["windows"])

    # 近 N 天的文章（判断"这周"用）
    dates = sorted({r["date"] for r in rows if r["date"]})
    recent_dates = set(dates[-recent_days:]) if dates else set()
    recent = [r for r in rows if r["date"] in recent_dates]
    recent_clean = [r for r in clean if r["date"] in recent_dates]

    ev = {
        "n_total": len(rows),
        "n_recent": len(recent),
        "days": len(dates),
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
        "rec_share": (sum(r["rec"] for r in rows) / sum(r["read"] for r in rows))
        if sum(r["read"] for r in rows)
        else 0.0,
        "blackout_windows": b["windows"],
        "n_blackout_rows": len(b["blackout_rows"]),
        "hit_rate_clean": (sum(1 for r in clean if r["rec"] >= HIT_REC) / len(clean))
        if clean
        else 0.0,
    }

    # 1) 数据不足 —— 明确说"还不能判断"，而不是给一个假结论
    if len(recent) < 2:
        return {
            "verdict": "INSUFFICIENT",
            "headline": "本周样本不足（%d 篇），现在给结论会是编的。" % len(recent),
            "actions": ["把本周发的文章数补到 ≥2 篇，我下轮再给判断"],
            "evidence": ev,
            "caveats": caveats,
        }

    # 2) 断供优先 —— 分发中断不是内容问题
    if in_black:
        wins = "、".join("%s~%s（%d 天）" % (w["from"], w["to"], w["days"]) for w in b["windows"])
        return {
            "verdict": "BLACKOUT",
            "headline": f"检测到推荐断供窗：{wins}。期间的零推荐是**分发中断**，不是内容问题。",
            "actions": [
                "先别改内容：断供期的数据不能用来评价选题或标题。",
                "查这 %d 天的账号状态：推荐/看一看入口是否被降权、是否有违规提示、"
                "是否触发了发文频率或内容重复限制。" % b["windows"][0]["days"],
                "复供后再用 2 周数据重算命中率，那时才谈结构调整。",
            ],
            "evidence": ev,
            "caveats": [
                *caveats,
                "断供判定：某日全部文章推荐阅读最大值 < 3 且当日 ≥2 篇，连续 ≥2 天记为一窗",
            ],
        }

    # 3) 家族差异 —— 组间差 ≥2 倍时，账号级结论无意义
    # 走共享内核 mpdt/analysis.py（R240）：与客户导出路径 diagnose.py 用同一套规则，
    # 否则同一个号在两条路上会得到矛盾结论，服务就没有可信度。
    fv = analysis.family_verdict(clean)
    ev["families"] = {
        k: {
            "n": v["n"],
            "rec_med": v["rec_med"],
            "rec_max": v["rec_max"],
            "hit_n": v["hit_n"],
            "hit_rate": v["hit_rate"],
            "spike": v["spike"],
        }
        for k, v in fv.get("usable", {}).items()
    }
    if fv["verdict"] in ("FAMILY_SPLIT", "FAMILY_SPARK"):
        return {
            "verdict": fv["verdict"],
            "headline": fv["headline"],
            "actions": fv["actions"],
            "evidence": ev,
            "caveats": caveats + fv.get("caveats", []),
        }

    # 4) 有推荐但不稳定
    hits = [r for r in recent_clean if r["rec"] >= HIT_REC]
    if len(recent_clean) >= 3 and len(hits) * 2 <= len(recent_clean):
        return {
            "verdict": "NO_SIGNAL",
            "headline": (
                "最近 %d 篇里只有 %d 篇拿到推荐（≥%d 人），"
                "账号当前基本吃不到平台推荐。" % (len(recent_clean), len(hits), HIT_REC)
            ),
            "actions": [
                "查推荐入口状态：账号权重、是否被限流、是否有历史违规。",
                "在这个状态下优化标题/封面是无效动作，先把分发通道修好。",
                "同时保留现有内容形态（消息渠道阅读 %d 人中位），别在推荐没恢复时大改。"
                % int(statistics.median([r["msg"] for r in recent_clean]) if recent_clean else 0),
            ],
            "evidence": ev,
            "caveats": caveats,
        }

    # 5) 正常
    return {
        "verdict": "NORMAL",
        "headline": (
            "最近 %d 篇里 %d 篇拿到推荐，推荐阅读占比 %.0f%%，"
            "分发正常，暂不需要结构调整。" % (len(recent_clean), len(hits), 100 * ev["rec_share"])
        ),
        "actions": [
            "保持现有节奏；本轮没有发现需要改的结构性问题。",
            "继续发满 %d 篇/周——样本太少时任何结论都会在下一周被推翻。" % max(2, len(recent)),
        ],
        "evidence": ev,
        "caveats": caveats,
    }


def render(adv: dict) -> str:
    """把判断渲染成周报里的 Markdown 节。"""
    lines = ["## 本周该做什么", ""]
    verdict_cn = {
        "INSUFFICIENT": "样本不足",
        "BLACKOUT": "分发断供",
        "FAMILY_SPLIT": "家族分化",
        "FAMILY_SPARK": "家族有孤峰",
        "NO_SIGNAL": "推荐缺位",
        "NORMAL": "分发正常",
    }
    lines.append("**判定：{}**".format(verdict_cn.get(adv.get("verdict"), adv.get("verdict"))))
    lines.append("")
    lines.append(adv.get("headline", ""))
    lines.append("")
    if adv.get("actions"):
        lines.append("**具体动作**")
        lines.append("")
        for i, a in enumerate(adv["actions"], 1):
            lines.append("%d. %s" % (i, a))
        lines.append("")
    ev = adv.get("evidence") or {}
    if ev:
        bits = []
        if ev.get("n_recent") is not None:
            bits.append(
                "近窗口 %d 篇 / 全期 %d 篇（%s ~ %s）"
                % (
                    ev.get("n_recent", 0),
                    ev.get("n_total", 0),
                    ev.get("date_from") or "?",
                    ev.get("date_to") or "?",
                )
            )
        if ev.get("rec_share") is not None:
            bits.append("推荐阅读占比 %.0f%%" % (100 * ev["rec_share"]))
        if ev.get("hit_rate_clean") is not None:
            bits.append("干净期命中率 %.0f%%" % (100 * ev["hit_rate_clean"]))
        if bits:
            lines.append("> 口径：{}。".format("；".join(bits)))
            lines.append("")
    for c in adv.get("caveats") or []:
        lines.append(f"> ⚠ {c}")
    if adv.get("caveats"):
        lines.append("")
    return "\n".join(lines)
