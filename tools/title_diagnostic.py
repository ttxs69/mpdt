#!/usr/bin/env python3
"""title_diagnostic — C20 X-title-rule / X-sxs-asset-coverage 自家号诊断工具

R140 落盘（2026-10-19）：mpdt publish_list 全量扫，按 rules.yaml external_rules.title
段长度带 20-35 / ban_patterns 跑硬约束诊断，按模板类豁免 vs 真·选题类两档分流，
输出英文实体保留率（X-sxs-asset-coverage 信号）。

用法：
    python3 tools/title_diagnostic.py [--days N] [--db /home/ubuntu/.mpdt/mpdt.sqlite3]

输出（stdout）：
- 长度带落带统计（distinct 标题，按模板/选题分流）
- ban_patterns 命中清单
- 英文实体保留率（真·选题类）
- 数字密度中位
- 真·选题类带外清单（与 PIC-rea/W42 单例反证对照）

依赖：pyyaml（解析 rules.yaml 的 external_rules.title.length_band）
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import yaml

DB_DEFAULT = "/home/ubuntu/.mpdt/mpdt.sqlite3"
RULES_PATH = (
    Path(__file__).resolve().parents[2] / "products" / "mp-operator-agent" / "engine" / "rules.yaml"
)


def title_len_chars(s: str) -> int:
    """中文 1 字 = 1，英文 token = 1（粗略）。"""
    n = 0
    for ch in s:
        if "\u4e00" <= ch <= "\u9fff" or (ch.isascii() and (ch.isalnum() or ch == "-")):
            n += 1
    return n


def load_external_rules(rules_path: Path):
    """从 rules.yaml 读 length_band / ban_patterns；失败回退 P17 默认值。"""
    if rules_path.exists():
        with rules_path.open(encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
        ext = (rules.get("external_rules") or {}).get("title") or {}
        return {
            "length_band": tuple(ext.get("length_band") or (20, 35)),
            "ban_patterns": ext.get("ban_patterns")
            or [
                "看完即删",
                "话说一半",
                "惊悚命令式",
                "假借官方",
                "无依据夸大",
            ],
        }
    return {
        "length_band": (20, 35),
        "ban_patterns": [
            "看完即删",
            "话说一半",
            "惊悚命令式",
            "假借官方",
            "无依据夸大",
        ],
    }


def fetch_publish_list(db_path: str, days: int) -> list:
    """从 mpdt.sqlite3 拉取 publish_list 快照的标题。返回 [{stat_date, title}]."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "SELECT DISTINCT stat_date, title FROM snapshots "
        "WHERE source='publish_list' AND title IS NOT NULL "
        "AND stat_date >= date('now', ?) "
        "ORDER BY stat_date DESC",
        (f"-{days} day",),
    )
    rows = c.fetchall()
    conn.close()
    return [{"stat_date": r[0], "title": r[1]} for r in rows]


def split_template_vs_topic(titles: list) -> tuple:
    """模板类（流水线自动生成） vs 真·选题类（个人选题）的分流。

    模板关键词：含「一览」「GH Trending」「GitHub / HN」「arxiv」「HF / PH」
    等流水线自动生成的标记即归入模板类。
    """
    TMPL_KW = ["一览", "GH Trending", "GitHub / HN", "arxiv", "HF / PH", "claude-plugins"]
    templates, topics = [], []
    for t in titles:
        if any(kw in t["title"] for kw in TMPL_KW):
            templates.append(t)
        else:
            topics.append(t)
    return templates, topics


def diagnose(titles: list, ext_rules: dict) -> dict:
    """主诊断：长度带 + ban_patterns + 英文实体保留 + 数字密度。"""
    LB = ext_rules["length_band"]
    BAN = ext_rules["ban_patterns"]
    ENG = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]+")
    DIG = re.compile(r"\d+(?:\.\d+)?")

    templates, topics = split_template_vs_topic(titles)

    def diag_bucket(bucket, label):
        in_band, out_short, out_long = [], [], []
        ban_hits = []
        eng_keep = []
        num_counts = []
        for entry in bucket:
            t = entry["title"]
            L = title_len_chars(t)
            if LB[0] > L:
                out_short.append((L, t))
            elif LB[1] < L:
                out_long.append((L, t))
            else:
                in_band.append((L, t))
            for bp in BAN:
                if bp in t:
                    ban_hits.append((bp, entry["stat_date"], t))
                    break
            if ENG.search(t):
                eng_keep.append(t)
            num_counts.append(len(DIG.findall(t)))
        total = len(bucket)
        coverage_in = len(eng_keep) / total if total else 0
        num_med = sorted(num_counts)[total // 2] if total else 0
        return {
            "label": label,
            "total": total,
            "length_band_in_count": len(in_band),
            "length_band_in_pct": len(in_band) / total if total else 0,
            "length_band_out_short": out_short,
            "length_band_out_long": out_long,
            "ban_hits": ban_hits,
            "eng_entity_keep_count": len(eng_keep),
            "eng_entity_keep_pct": coverage_in,
            "num_density_median": num_med,
            "eng_entity_keep_list": eng_keep,
        }

    return {
        "templates": diag_bucket(templates, "模板类（流水线自动生成·豁免）"),
        "topics": diag_bucket(topics, "真·选题类（个人选题·诊断生效）"),
        "length_band": list(LB),
        "ban_patterns_count": len(BAN),
    }


def render_md(result: dict, days: int) -> str:
    out = []
    out.append(f"# C20 title_diagnostic · 自家号诊断报告（R140 · {days} 天扫描）")
    out.append("")
    LB = result["length_band"]
    out.append(f"长度带 = {LB[0]}-{LB[1]} 字 · ban_patterns = {result['ban_patterns_count']} 条")
    out.append("")

    for key in ("templates", "topics"):
        sec = result[key]
        out.append(f"## {sec['label']}  ·  {sec['total']} 条")
        out.append("")
        out.append(
            f"- 长度带内 = {sec['length_band_in_count']} / {sec['total']} = {sec['length_band_in_pct'] * 100:.1f}%"
        )
        out.append(
            f"- 英文实体保留 = {sec['eng_entity_keep_count']} / {sec['total']} = {sec['eng_entity_keep_pct'] * 100:.1f}%"
        )
        out.append(f"- 数字密度中位 = {sec['num_density_median']} 个/标题")
        if sec["length_band_out_short"]:
            out.append(f"\n### 短标题（<{LB[0]} 字）")
            for L, t in sec["length_band_out_short"]:
                out.append(f"- {L} 字 | {t}")
        if sec["length_band_out_long"]:
            out.append(f"\n### 长标题（>{LB[1]} 字）")
            for L, t in sec["length_band_out_long"]:
                out.append(f"- {L} 字 | {t}")
        if sec["ban_hits"]:
            out.append(f"\n### ban_patterns 命中（{len(sec['ban_hits'])} 条）")
            for bp, d, t in sec["ban_hits"]:
                out.append(f"- [{bp}] {d} | {t}")
        if sec.get("eng_entity_keep_list"):
            out.append(f"\n### 英文实体保留（{len(sec['eng_entity_keep_list'])} 条）")
            for t in sec["eng_entity_keep_list"][:8]:
                out.append(f"- {t}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="C20 X-title-rule / X-sxs-asset-coverage 自家号诊断")
    ap.add_argument("--days", type=int, default=30, help="拉取快照的天数窗口（默认 30）")
    ap.add_argument("--db", default=DB_DEFAULT, help="mpdt sqlite 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown")
    args = ap.parse_args()

    ext_rules = load_external_rules(RULES_PATH)
    titles = fetch_publish_list(args.db, args.days)
    if not titles:
        print(f"[!] 库里 {args.days} 天内无 publish_list 标题（db={args.db}）", file=sys.stderr)
        sys.exit(2)

    result = diagnose(titles, ext_rules)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_md(result, args.days))


if __name__ == "__main__":
    main()
