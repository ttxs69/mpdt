"""指标口径注册表（语义层）：以"概念 + 系列"为身份，窗口是身份的一部分。

参考 dbt metrics / MetricFlow / Prometheus 命名哲学：
  - 同一业务概念（完读率 / 阅读量 / 分享 / …）在多源各有一个 series；
  - series_key 由源缩写 + 窗口天数拼出（dc7 / ad30 / pl），是 series 的稳定标识；
  - 易混淆配对 = 同一概念下 ≥2 个 series，自动从 window/denominator 差异生成 why。

源窗口速查：
  publish_list     实时累计（拉取时点全量，无窗口）
  datacube_article 发表后 7 天窗口，T+1 出数（refdate 为文章口径日）
  article_detail   发表后 30 天窗口（页面口径自 2025-11-01 起）

唯一真相源：本模块的 CONCEPTS；report / CLI / README / CSV 元数据全部由本模块派生。
"""

from collections.abc import Iterable, Iterator
from itertools import combinations
from typing import Any

# 源缩写（series_key 后缀） & 窗口字面量
SRC_PL = "publish_list"
SRC_DC = "datacube_article"
SRC_AD = "article_detail"

W_PL = "实时累计（拉取时点全量）"
W_DC = "发表后 7 天窗口，T+1 出数"
W_AD = "发表后 30 天窗口"

WIN_DAYS = {SRC_PL: 0, SRC_DC: 7, SRC_AD: 30}  # pl=0（无窗口）仅用于 series_key 后缀与 why 文本
SERIES_SUFFIX = {SRC_PL: "pl", SRC_DC: "dc7", SRC_AD: "ad30"}

SOURCE_DESC = {
    SRC_PL: "发表列表（/cgi-bin/appmsgpublish）",
    SRC_DC: "datacube 内容分析-文章级（/misc/datacubequery tmpl=17）",
    SRC_AD: "单篇渠道页（/misc/appmsganalysis detailpage）",
}

# ── 概念分类树 ──────────────────────────────────────────────
# 概念 canonical_name → {
#   display, unit, note,
#   series: { series_key: { source, field, window, window_days, denominator, note, deprecated? } }
# }
#
# series_key = f"{concept_canonical}@{series_key_short}"，实际写入
# 的 series_key 形如 'dc7' / 'ad30' / 'pl'（不带概念前缀，概念已由外层 dict key 表达）。
#
# 命名哲学：series_key 是"该概念在某个 (源, 窗口) 切片下的稳定标识符"；
# 不同概念下相同的源+窗口也可能撞 series_key，所以存取值一律走
# `CONCEPTS[cname]['series'][series_key]`，不要把 series_key 当全局唯一键。
#
# 「deprecated: true」标记保留登记但不出现在：
#   - auto_pairs（不让它参与配对）
#   - cross_source_table 宽表（不让它占一列）
#   - 导出元数据 *.metrics.csv 的元数据命中（仍写「(未登记)」，但导出本身不变）
#   - to_concepts_view / readme_section / calibration_note
CONCEPTS: dict[str, dict[str, Any]] = {
    "read_completion": {
        "display": "完读率",
        "unit": "比例(0-1)",
        "note": "完读率是同一概念下的两个口径，窗口与分母均不同",
        "series": {
            "dc7": {
                "source": SRC_DC,
                "field": "finish_read_rate",
                "window": W_DC,
                "window_days": 7,
                "denominator": "7 天窗口内阅读人数",
                "note": "datacube 7 天窗口·阅读人数分母",
            },
            "ad30": {
                "source": SRC_AD,
                "field": "finished_read_pv_ratio",
                "window": W_AD,
                "window_days": 30,
                "denominator": "阅读次数(pv)",
                "note": "单篇页 30 天窗口·pv 分母",
            },
        },
    },
    "read_volume": {
        "display": "阅读量",
        "unit": "人次/次/人（按 series 而定）",
        "note": "三个 series 口径不同：实时累计 vs 7 天 pv vs 30 天 uv",
        "series": {
            "pl": {
                "source": SRC_PL,
                "field": "read_num",
                "window": W_PL,
                "window_days": 0,
                "denominator": "-",
                "note": "publish_list 实时累计，无窗口无去重",
            },
            "dc7": {
                "source": SRC_DC,
                "field": "total_pv",
                "window": W_DC,
                "window_days": 7,
                "denominator": "-",
                "note": "datacube 7 天窗口·全渠道阅读次数（≈量级）",
            },
            "ad30": {
                "source": SRC_AD,
                "field": "read_uv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "「全部」渠道去重",
                "note": "单篇页 30 天窗口·全渠道去重阅读人数",
            },
        },
    },
    "share": {
        "display": "分享",
        "unit": "次/人（按 series 而定）",
        "series": {
            "pl": {
                "source": SRC_PL,
                "field": "share_num",
                "window": W_PL,
                "window_days": 0,
                "denominator": "-",
                "note": "实时累计分享次数",
            },
            "dc7": {
                "source": SRC_DC,
                "field": "total_share",
                "window": W_DC,
                "window_days": 7,
                "denominator": "-",
                "note": "datacube 7 天窗口·分享次数",
            },
            "ad30": {
                "source": SRC_AD,
                "field": "share_uv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·分享人数",
            },
        },
    },
    "like": {
        "display": "点赞",
        "unit": "次",
        "series": {
            "pl": {
                "source": SRC_PL,
                "field": "like_num",
                "window": W_PL,
                "window_days": 0,
                "denominator": "-",
                "note": "实时累计点赞数",
            },
            "ad30": {
                "source": SRC_AD,
                "field": "like_cnt",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·点赞数",
            },
        },
    },
    "wow": {
        "display": "在看",
        "unit": "次",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "zaikan_cnt",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·「在看」数（与点赞不同概念）",
            },
        },
    },
    "comment": {
        "display": "评论",
        "unit": "条",
        "series": {
            "pl": {
                "source": SRC_PL,
                "field": "comment_num",
                "window": W_PL,
                "window_days": 0,
                "denominator": "-",
                "note": "实时累计评论数",
            },
            "ad30": {
                "source": SRC_AD,
                "field": "comment_cnt",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·评论数",
            },
        },
    },
    "delivery": {
        "display": "送达人数",
        "unit": "人",
        "series": {
            "dc7": {
                "source": SRC_DC,
                "field": "send_succ",
                "window": W_DC,
                "window_days": 7,
                "denominator": "-",
                "note": "datacube 7 天窗口·送达人数（分母=送达）",
            },
        },
    },
    "finish_count": {
        "display": "完读次数",
        "unit": "次",
        "series": {
            "dc7": {
                "source": SRC_DC,
                "field": "total_read_finish",
                "window": W_DC,
                "window_days": 7,
                "denominator": "-",
                "note": "datacube 7 天窗口·完读次数",
            },
        },
    },
    "read_rate": {
        "display": "阅读率",
        "unit": "比例(0-1)",
        "series": {
            "dc7": {
                "source": SRC_DC,
                "field": "read_rate",
                "window": W_DC,
                "window_days": 7,
                "denominator": "送达人数(send_succ)",
                "note": "datacube 阅读 pv / 送达",
            },
        },
    },
    "follow_after_read": {
        "display": "阅读后关注人数",
        "unit": "人",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "follow_after_read_uv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·阅读后关注人数",
            },
        },
    },
    "collection": {
        "display": "收藏人数",
        "unit": "人",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "collection_uv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·收藏人数",
            },
        },
    },
    "listen_people": {
        "display": "听读人数",
        "unit": "人",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "listen_uv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·听读人数（与听读次数不同概念）",
            },
        },
    },
    "listen_plays": {
        "display": "听读次数",
        "unit": "次",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "listen_pv",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·听读次数（与听读人数不同概念）",
            },
        },
    },
    "avg_read_time": {
        "display": "平均阅读时长",
        "unit": "秒",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "avg_article_read_time",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·平均阅读时长（秒）",
            },
        },
    },
    "praise_money": {
        "display": "赞赏金额",
        "unit": "上游金额单位",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "praise_money",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·赞赏金额",
            },
        },
    },
}

# ── 元数据注册表 ────────────────────────────────────────────
# 派生列 / 快照日 / 结构列：被 fetchers 入库但**不是指标概念**——它们没有"窗口/分母"
# 概念，是被算出来的辅助列。仍登记口径明确"非指标"语义，从 CONCEPTS 移出后：
#   - 不出现在 to_concepts_view / auto_pairs / cross_source_table / calibration_note
#   - 不参与「跨 series 对照」
#   - 仍被 find_series() / csv_metric_meta() 查询到，CSV 导出元数据继续可用
# display 字段标注「派生列」「结构列」以便人读。
META_FIELDS: dict[str, dict[str, Any]] = {
    "refdate": {
        "display": "文章口径日",
        "kind": "snapshot",
        "unit": "日期",
        "series": {
            "dc7": {
                "source": SRC_DC,
                "field": "refdate",
                "window": W_DC,
                "window_days": 7,
                "denominator": "-",
                "note": "文章口径日（快照行 stat_date 是拉取日，两者不同）",
            },
        },
    },
    "publish_date": {
        "display": "发表日",
        "kind": "snapshot",
        "unit": "日期",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "publish_date",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "单篇页 30 天窗口·文章发表日",
            },
        },
    },
    "window_days": {
        "display": "窗口天数",
        "kind": "derived",
        "unit": "天",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "window_days",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "mpdt 写入的派生列（页面固定 30 天）",
            },
        },
    },
    "total_read_user": {
        "display": "全渠道阅读人数(汇总)",
        "kind": "derived",
        "unit": "人",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "total_read_user",
                "window": W_AD,
                "window_days": 30,
                "denominator": "-",
                "note": "mpdt 派生列：「全部」渠道逐日 read_user 求和",
            },
        },
    },
    "channel_breakdown": {
        "display": "渠道构成",
        "kind": "structured",
        "unit": "JSON",
        "series": {
            "ad30": {
                "source": SRC_AD,
                "field": "channel_breakdown",
                "window": W_AD,
                "window_days": 30,
                "denominator": "「全部」渠道阅读人数",
                "note": "mpdt 派生列；同一用户多渠道重复计数 → 占比之和可 >100%（仅「全部」去重）",
            },
        },
    },
}

# ── 旧版字段：保留登记 + 显式标记 deprecated ──────────────────
# 旧 like_num 字段 `old_like_num`（publish_list 上报），概念上与新 like_num 等价；
# 标记 deprecated 后：从 auto_pairs / cross_source_table / 导出元数据 / metrics 视图全部排除。
DEPRECATED: dict[str, dict[str, Any]] = {
    "old_like_num": {
        "display": "旧版点赞数",
        "replaced_by": "like_num",
        "series": {
            "pl2": {
                "source": SRC_PL,
                "field": "old_like_num",
                "window": W_PL,
                "window_days": 0,
                "denominator": "-",
                "note": "旧版字段（保留登记），口径与 like_num 相当",
            },
        },
    },
}

SOURCES = (SRC_PL, SRC_DC, SRC_AD)


# ── 派生 API（从 CONCEPTS 推导，调用方只读这一套） ──────────


def concepts() -> dict[str, Any]:
    """返回整个概念树（不可变视图，调用方勿改）。"""
    return CONCEPTS


def all_series() -> Iterator[tuple[str, str, dict[str, Any]]]:
    """展开 CONCEPTS → 平铺 series 元组流（每个 (concept, series_key, series_dict)）。

    排除 deprecated（不参与对照/配对）。META_FIELDS 是非指标独立表，不在此流。
    """
    for cname, c in CONCEPTS.items():
        for skey, s in c["series"].items():
            if s.get("deprecated"):
                continue
            yield cname, skey, s


def _meta_series() -> Iterator[tuple[str, str, dict[str, Any]]]:
    """META_FIELDS 摊平成 (mname, skey, series)。"""
    for mname, m in META_FIELDS.items():
        for skey, s in m["series"].items():
            yield mname, skey, s


def _deprecated_series() -> Iterator[tuple[str, str, dict[str, Any]]]:
    """DEPRECATED 摊平成 (dname, skey, series)。"""
    for dname, d in DEPRECATED.items():
        for skey, s in d["series"].items():
            yield dname, skey, s


def _match_series(
    entries: Iterable[tuple[str, str, dict[str, Any]]], field: str | None, source: str | None
) -> tuple[str, str, dict[str, Any]] | None:
    """在 (name, skey, series) 序列里找第一个匹配；没有返回 None。"""
    for name, skey, s in entries:
        if field is not None and s["field"] != field:
            continue
        if source is not None and s["source"] != source:
            continue
        return name, skey, s
    return None


def find_series(
    field: str | None = None, source: str | None = None
) -> tuple[str, str, dict[str, Any]] | None:
    """按 field 查 series（field 在 (source, field) 上唯一）；可同时按 source 限定。

    查询顺序：CONCEPTS → META_FIELDS → DEPRECATED。
    返回 (concept_or_meta_key, series_key, series_dict)。

    注意 series_key 必须来自**命中的那条**：早先 META/DEPRECATED 两段用 `_skey`
    接收循环变量却 `return skey`，返回的是上一段循环残留的键 —— 实测
    find_series("refdate","dc7") 曾返回 ('refdate','ad30',<dc7 的 series>)。
    """
    for entries in (all_series(), _meta_series(), _deprecated_series()):
        hit = _match_series(entries, field, source)
        if hit is not None:
            return hit
    return None


def auto_pairs() -> tuple[tuple[str, str, str, str], ...]:
    """易混淆配对：同概念 ≥2 series 即一对；why 由 window/denominator 差异自动生成。

    deprecated / META_FIELDS 不参与。返回 tuple of (concept_name, series_a_key,
    series_b_key, why_text)。
    """
    out = []
    for cname, c in CONCEPTS.items():
        keys = [k for k, s in c["series"].items() if not s.get("deprecated")]
        if len(keys) < 2:
            continue
        for a, b in combinations(keys, 2):
            sa, sb = c["series"][a], c["series"][b]
            diffs = []
            if sa["window"] != sb["window"]:
                diffs.append("窗口 {} vs {}".format(sa["window"], sb["window"]))
            if sa["denominator"] != sb["denominator"]:
                diffs.append("分母「{}」 vs 「{}」".format(sa["denominator"], sb["denominator"]))
            if not diffs:
                diffs.append("同窗口·同分母（仍按概念聚合）")
            why = "同一概念「{}」两个 series：{}，数值不可直接比".format(
                c["display"], "；".join(diffs)
            )
            out.append((cname, a, b, why))
    return tuple(out)


# ── 人读 / 文档派生 ─────────────────────────────────────────


def to_concepts_view(query: str | None = None) -> list[Any]:
    """按概念分组的人读视图（`mpdt metrics` data 体；`mpdt metrics <query>` 过滤）。

    每个概念节点：{ concept, display, unit, series: [{key, source, field, window,
    window_days, denominator, note}] }。
    query 在 concept/display/source/field 子串上匹配；任一 series 命中即保留整个概念。
    不含 META_FIELDS / DEPRECATED。
    """
    q = (query or "").strip().lower()
    out = []
    for cname, c in CONCEPTS.items():
        series_out = []
        for skey, s in c["series"].items():
            if s.get("deprecated"):
                continue
            series_out.append(
                {
                    "key": skey,
                    "source": s["source"],
                    "field": s["field"],
                    "window": s["window"],
                    "window_days": s["window_days"],
                    "denominator": s["denominator"],
                    "note": s["note"],
                }
            )
        hay = " ".join(
            [cname, c["display"], c.get("unit", "")]
            + [s["source"] + " " + s["field"] for s in c["series"].values()]
        ).lower()
        if q and q not in hay:
            continue
        out.append(
            {
                "concept": cname,
                "display": c["display"],
                "unit": c["unit"],
                **({"note": c["note"]} if c.get("note") else {}),
                "series": series_out,
            }
        )
    return out


def calibration_note() -> str:
    """周报文首口径说明（由 CONCEPTS 生成）。

    按概念列：一行一个概念，含该概念下各 series 一句话口径。
    「同概念多 series 不可直接比」统一在节首提一次，概念行不重复。
    """
    multi_series_count = sum(
        1
        for c in CONCEPTS.values()
        if sum(1 for s in c["series"].values() if not s.get("deprecated")) >= 2
    )
    lines = ["> **口径说明（按概念分组，由 mpdt/metrics.py 概念树生成）**"]
    if multi_series_count:
        lines.append(
            "> 同概念多 series 的对照见文末「跨 series 对照」节，"
            "窗口/分母不同时**数值不可直接比**。"
        )
    for _cname, c in CONCEPTS.items():
        parts = []
        for skey, s in c["series"].items():
            if s.get("deprecated"):
                continue
            parts.append(f"`{skey}`=「{s['field']}」·{s['window_days']}天")
        if not parts:
            continue
        lines.append("> - **{}**（{}）：{}".format(c["display"], c["unit"], "；".join(parts)))
    lines.append(
        "> 元数据字段（refdate / publish_date / window_days / total_read_user / "
        "channel_breakdown）见 README 「指标口径」节；它们非指标，不参与对照。"
    )
    return "\n".join(lines)


def _concept_table_lines(c: dict[str, Any]) -> list[Any]:
    """一个概念的 series 表格（跳过 deprecated）。"""
    out = ["", "| series_key | 源字段 | 所属源 | 窗口 | 窗口天数 | 分母 | 备注 |"]
    out.append("|---|---|---|---|---|---|---|")
    for skey, s in c["series"].items():
        if s.get("deprecated"):
            continue
        out.append(
            "| `{}` | `{}` | {} | {} | {} | {} | {} |".format(
                skey,
                s["field"],
                s["source"],
                s["window"],
                s["window_days"],
                s["denominator"],
                s.get("note", ""),
            )
        )
    return out


def _meta_table_lines() -> list[Any]:
    """元数据字段表（非指标）。"""
    out = ["| 规范名 | 字段 | 源 | 窗口 | 类型 | 备注 |", "|---|---|---|---|---|---|"]
    for mname, _skey, s in _meta_series():
        out.append(
            "| `{}` | `{}` | {} | {} | {} | {} |".format(
                mname,
                s["field"],
                s["source"],
                s["window"],
                META_FIELDS[mname]["kind"],
                s.get("note", ""),
            )
        )
    return out


def _deprecated_lines() -> list[Any]:
    """旧版字段一行一条：原名 → 替代名：备注。"""
    out = []
    for dname, _skey, s in _deprecated_series():
        replaced = DEPRECATED[dname].get("replaced_by", "?")
        out.append("- `{}` → `{}`：{}".format(dname, replaced, s.get("note", "")))
    return out


def _pair_table_lines() -> list[Any]:
    """易混淆配对表：同概念的 C(N,2) 对折叠成一行。"""
    by_cname: dict[str, list[tuple[str, str]]] = {}
    for cname, a, b, _why in auto_pairs():
        by_cname.setdefault(cname, []).append((a, b))
    out = ["| 概念 | 不可直接比的 series 配对 | 主要差异 |", "|---|---|---|"]
    for cname, pairs in by_cname.items():
        display = CONCEPTS[cname]["display"]
        # 用 a vs b；a vs c；b vs c 形式压缩
        pair_str = "；".join(f"`{a}` vs `{b}`" for a, b in pairs)
        # 主要差异：所有 pair 共有的差异点（窗口或分母）合并
        diff_set = set()
        for a, b in pairs:
            sa, sb = CONCEPTS[cname]["series"][a], CONCEPTS[cname]["series"][b]
            if sa["window"] != sb["window"]:
                diff_set.add("窗口")
            if sa["denominator"] != sb["denominator"]:
                diff_set.add("分母")
        diff_str = "、".join(sorted(diff_set)) or "同窗口·同分母"
        out.append(f"| {display}（`{cname}`，{len(pairs)} 对） | {pair_str} | {diff_str} |")
    return out


def readme_section() -> str:
    """README『指标口径』节（按概念分组；改 CONCEPTS 后重新生成本节保持同步）。"""
    out = ["## 指标口径（CONCEPTS · 按概念分组）", ""]
    for cname, c in CONCEPTS.items():
        out.append("#### {}（`{}`，单位：{}）".format(c["display"], cname, c["unit"]))
        if c.get("note"):
            out.append("> " + c["note"])
        out.extend(_concept_table_lines(c))
        out.append("")
    out.append("### 元数据字段（非指标：快照日 / 派生列 / 结构列）")
    out.append("")
    out.extend(_meta_table_lines())
    out.append("")
    out.append("### 旧版字段（保留登记；不参与对照 / 不出现在 metrics 视图）")
    out.append("")
    out.append(
        "> 旧版字段保留在注册表里是为了让 `mpdt/metrics.py` 仍能识别上游接口"
        "历史返回的字段（CSV 导出元数据 `*.metrics.csv` 会标出口径），"
        "但不参与周报对照、metrics 视图与自动配对。"
    )
    out.extend(_deprecated_lines())
    out.append("")
    out.append("### 自动推导的易混淆配对（同一概念多 series）")
    out.append("")
    # 按概念聚合：C(N,2) 对折叠成一行表格
    out.extend(_pair_table_lines())
    out.append("")
    out.append(
        "> 本节由 `mpdt/metrics.py` CONCEPTS 自动生成（"
        '`python3 -c "from mpdt.metrics import readme_section; print(readme_section())"`）。'
        "改口径请改 CONCEPTS，再运行 `mpdt metrics` 校对。"
    )
    return "\n".join(out)
