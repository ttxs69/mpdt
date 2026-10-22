"""report 单元测试（T2）：_week_bounds / _md_table / generate（含空数据周）/ 跨 series 宽表对照。

运行：python3 tests/test_report.py 或 unittest discover  （零第三方依赖）
"""

import os
import sys
import tempfile
import unittest
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import (
    report,
    store,
)


def _detail_metrics(
    read_uv: Any, fr: Any, pub="2026-10-12", rec_pct=45.0, msg_pct=30.0, srch_pct=8.0
) -> Any:
    """造 article_detail 快照 metrics（与 cmd_detail 入库字段对齐）。"""
    return {
        "read_uv": read_uv,
        "finished_read_pv_ratio": fr,
        "avg_article_read_time": 12,
        "publish_date": pub,
        "channel_breakdown": [
            {"scene": 6, "scene_label": "推荐", "read_user": 10, "read_pct": rec_pct},
            {"scene": 0, "scene_label": "公众号消息", "read_user": 7, "read_pct": msg_pct},
            {"scene": 7, "scene_label": "搜一搜", "read_user": 2, "read_pct": srch_pct},
        ],
    }


class TestWeekBounds(unittest.TestCase):
    def test_midyear_week(self) -> None:
        monday, sunday = report._week_bounds("2026-W42")
        self.assertEqual(monday.isoformat(), "2026-10-12")
        self.assertEqual(sunday.isoformat(), "2026-10-18")

    def test_year_boundary_into_next_year(self) -> None:
        monday, sunday = report._week_bounds("2026-W01")
        self.assertEqual(monday.isoformat(), "2025-12-29")
        self.assertEqual(sunday.isoformat(), "2026-01-04")

    def test_week53_crosses_year_end(self) -> None:
        monday, sunday = report._week_bounds("2020-W53")
        self.assertEqual(monday.isoformat(), "2020-12-28")
        self.assertEqual(sunday.isoformat(), "2021-01-03")

    def test_dayspan_is_seven(self) -> None:
        monday, sunday = report._week_bounds("2024-W09")
        self.assertEqual((sunday - monday).days, 6)


class TestMdTable(unittest.TestCase):
    def test_header_separator_and_rows(self) -> None:
        md = report._md_table(["文章", "完读率"], [["甲", 0.25], ["乙", 0.5]])
        lines = md.splitlines()
        self.assertEqual(lines[0], "| 文章 | 完读率 |")
        self.assertEqual(lines[1], "|---|---|")
        self.assertEqual(lines[2], "| 甲 | 0.25 |")
        self.assertEqual(lines[3], "| 乙 | 0.5 |")

    def test_empty_rows_only_header(self) -> None:
        md = report._md_table(["a"], [])
        self.assertEqual(md.splitlines(), ["| a |", "|---|"])


class ReportBase(unittest.TestCase):
    def setUp(self) -> Any:
        self._tmp = tempfile.TemporaryDirectory()
        self.con = store.connect(os.path.join(self._tmp.name, "db.sqlite3"))
        self.out_dir = os.path.join(self._tmp.name, "reports")
        # fixture：W42（2026-10-12 ~ 10-18）内三篇 datacube 文章 + 两篇 article_detail
        for i, (am, fr) in enumerate([("mA", 0.10), ("mB", 0.40), ("mC", 0.25)]):
            store.upsert(
                self.con,
                "datacube_article",
                "2026-10-1%d" % (2 + i),
                am,
                1,
                "文章" + am,
                {
                    "refdate": "2026-10-1%d" % (2 + i),
                    "finish_read_rate": fr,
                    "read_rate": 0.5,
                    "total_pv": 100 + i,
                    "total_share": 3,
                    "total_read_finish": 0 if fr == 0.10 else 10,
                },
            )
        store.upsert(
            self.con, "article_detail", "2026-10-17", "mA", 1, "文章mA", _detail_metrics(190, 0.25)
        )
        store.upsert(
            self.con,
            "article_detail",
            "2026-10-16",
            "mB",
            1,
            "文章mB",
            _detail_metrics(500, 0.31, pub="2026-10-13"),
        )
        # 同步 publish_list 也造两条，让 read_volume 的 pl vs dc7 配对有数据
        store.upsert(
            self.con,
            "publish_list",
            "2026-10-17",
            "mA",
            1,
            "文章mA",
            {"read_num": 80, "share_num": 5, "like_num": 1, "old_like_num": 1, "comment_num": 0},
        )
        store.upsert(
            self.con,
            "publish_list",
            "2026-10-16",
            "mB",
            1,
            "文章mB",
            {"read_num": 220, "share_num": 8, "like_num": 2, "old_like_num": 1, "comment_num": 1},
        )
        self.con.commit()

    def tearDown(self) -> Any:
        self.con.close()
        self._tmp.cleanup()


class TestCrossSourceTable(ReportBase):
    """跨 series 对照：每概念一张宽表（不再 C(N,2) 张两两对照）。"""

    def test_returns_one_block_per_multi_series_concept(self) -> None:
        blocks = report.cross_source_table(self.con)
        # 每概念至多一块（不再多张两两对照表）
        seen = {}
        for b in blocks:
            self.assertNotIn(b["concept"], seen, "{} 应只有一块".format(b["concept"]))
            seen[b["concept"]] = b
        # 至少要含完读率/阅读量/分享/点赞/评论——listen 已拆走不再有配对
        concepts_seen = {b["concept"] for b in blocks}
        for c in ("read_completion", "read_volume", "share", "like", "comment"):
            self.assertIn(c, concepts_seen, f"跨 series 对照应含概念 {c}")
        # listen 不再以「一对」出现（已拆 listen_people/listen_plays 各 1 series）
        self.assertNotIn("listen", concepts_seen)
        # metadata 概念也不在
        for meta in (
            "refdate",
            "publish_date",
            "window_days",
            "total_read_user",
            "channel_breakdown",
        ):
            self.assertNotIn(meta, concepts_seen)

    def test_finish_read_wide_table_shape(self) -> None:
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b["concept"] == "read_completion")
        # 宽表：列 = 文章 / appmsgid_itemidx / dc7值 / ad30值 / 差值(两两差)
        self.assertIn("文章", blk["header"])
        self.assertIn("appmsgid_itemidx", blk["header"])
        self.assertIn("完读率(dc7·7天)", blk["header"])
        self.assertIn("完读率(ad30·30天)", blk["header"])
        self.assertIn("差值(百分点·两两差)", blk["header"])
        # fixture：mA 与 mB 两篇同时有 dc7 和 ad30
        self.assertEqual(len(blk["rows"]), 2)
        by_key = {r[1]: r for r in blk["rows"]}
        # mA：dc 0.10 vs ad 0.25 → 行末差值 -15.0
        self.assertEqual(by_key["mA_1"][2], "10.0%")
        self.assertEqual(by_key["mA_1"][3], "25.0%")
        self.assertIn("−", by_key["mA_1"][4])  # 差值列用全角负号
        self.assertIn("-15.0pt", by_key["mA_1"][4])
        # mB：dc 0.40 vs ad 0.31 → +9.0
        self.assertIn("+9.0pt", by_key["mB_1"][4])

    def test_read_volume_wide_table_not_percent(self) -> None:
        # 阅读量 wide 表：pl=80 vs dc7=100 不会有 % 符号
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b["concept"] == "read_volume")
        self.assertTrue(blk["rows"])
        for r in blk["rows"]:
            for cell in r[2:]:
                self.assertNotIn("%", cell)
        # 阅读量不是 ratio，不应有「差值(百分点·两两差)」列
        for h in blk["header"]:
            self.assertNotIn("百分点", h)

    def test_note_warns_not_comparable(self) -> None:
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b["concept"] == "read_completion")
        self.assertIn("不可直接比", blk["note"])
        # series key 在 note 里；窗口/分母差异要点出
        self.assertIn("dc7", blk["note"])
        self.assertIn("ad30", blk["note"])
        self.assertIn("7 天", blk["note"])
        self.assertIn("30 天", blk["note"])

    def test_no_overlap_marks_has_data_false(self) -> None:
        con2 = store.connect(os.path.join(self._tmp.name, "mem.sqlite3"))
        store.upsert(
            con2, "datacube_article", "2026-10-15", "mA", 1, "a", {"finish_read_rate": 0.1}
        )
        store.upsert(
            con2, "article_detail", "2026-10-16", "mZ", 1, "z", {"finished_read_pv_ratio": 0.2}
        )
        blocks = report.cross_source_table(con2)
        blk = next(b for b in blocks if b["concept"] == "read_completion")
        self.assertEqual(blk["rows"], [])
        self.assertFalse(blk["has_data"])
        con2.close()

    def test_latest_snapshot_wins_per_key(self) -> None:
        # mA 在 article_detail 有 10-16 与 10-17 两个快照 → 取 10-17（0.25）
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b["concept"] == "read_completion")
        by_key = {r[1]: r for r in blk["rows"]}
        self.assertEqual(by_key["mA_1"][3], "25.0%")

    def test_missing_value_excluded_from_wide_row(self) -> None:
        # mC 在 article_detail 没有 finished_read_pv_ratio → 不进 read_completion 宽表
        store.upsert(self.con, "article_detail", "2026-10-17", "mC", 1, "文章mC", {"read_uv": 10})
        self.con.commit()
        blocks = report.cross_source_table(self.con)
        blk = next(b for b in blocks if b["concept"] == "read_completion")
        self.assertEqual(len(blk["rows"]), 2)

    def test_concept_filter_returns_only_matching(self) -> None:
        blocks = report.cross_source_table(self.con, concept="read_completion")
        self.assertEqual({b["concept"] for b in blocks}, {"read_completion"})

    def test_no_hardcoded_finish_read_rate_only(self) -> None:
        # 回归保护：cross_source_table 必须泛化到 ≥2 个概念
        blocks = report.cross_source_table(self.con)
        self.assertGreaterEqual(len({b["concept"] for b in blocks}), 2)

    def test_one_block_per_concept_not_C_N_2(self) -> None:
        # 之前点赞概念 4 series → C(4,2)=6 张表；现在每概念只 1 张
        blocks = report.cross_source_table(self.con)
        like_blocks = [b for b in blocks if b["concept"] == "like"]
        self.assertEqual(len(like_blocks), 1, "like 概念应只 1 张宽表（不再 6 张）")
        # like 宽表应只有 pl + ad30 两列数据（不出现 ad30z/zaikan_cnt）
        like = like_blocks[0]
        field_columns = [h for h in like["header"] if "·" in h]
        self.assertEqual(len(field_columns), 2, "like 宽表只 2 个 series 列")
        for h in field_columns:
            self.assertNotIn("zaikan_cnt", h)
            self.assertNotIn("old_like_num", h)


class TestHtmlTemplate(ReportBase):
    """阶段 6 打磨：HTML 模板化（md 子集渲染器 + 移动端 CSS）。

    P3 纪律：每个断言对应「机制生效」——表格渲染、列对齐、引用块、
    HTML 实体转义（防注入标题）、分隔行剔除。
    """

    def test_table_cell_count_alignment(self) -> None:
        """数据行列数不足/超出表头时补空/截断，不错位。"""
        html = report._md_table_html("| a | b | c |", ["| 1 |", "| 1 | 2 | 3 | 4 |"])
        self.assertEqual(html.count("<td>"), 6)  # 2+3=6（补1空、截1多）
        self.assertIn("<td></td>", html)

    def test_md_to_html_structure(self) -> None:
        md = (
            "# 标题\n\n## 小节\n\n> 引用一\n> 引用二\n\n| H1 | H2 |\n|---|---|\n"
            "| a | b |\n\n普通段落 **加粗** 与 `code`。\n"
        )
        h = report._md_to_html(md)
        self.assertIn("<h1>标题</h1>", h)
        self.assertIn('<h2 id="sec-0">小节</h2>', h)
        self.assertIn("<blockquote>引用一<br>引用二</blockquote>", h)
        self.assertIn("<th>H1</th>", h)
        self.assertIn("<td>a</td>", h)
        self.assertIn("<strong>加粗</strong>", h)
        self.assertIn("<code>code</code>", h)
        self.assertNotIn("|", h.split("</table>")[0].split("<table>")[1])
        self.assertNotIn("---", h)

    def test_escaping_title_with_html(self) -> None:
        """标题带 <script> 等内容必须被转义（html_mod.escape 先于一切行内替换）。"""
        from mpdt import store as store_mod

        con = store_mod.connect(os.path.join(self.out_dir, "x.sqlite3"))
        store_mod.upsert(
            con,
            "datacube_article",
            "2026-10-15",
            1,
            1,
            "<script>alert(1)</script>",
            {
                "finish_read_rate": 0.2,
                "read_rate": 0.3,
                "total_pv": 5,
                "total_share": 1,
                "refdate": "2026-10-15",
            },
        )
        con.commit()
        out = report.generate(con, "2026-W42", self.out_dir)
        h = open(out["html"]).read()
        self.assertNotIn("<script>alert", h)
        self.assertIn("&lt;script&gt;", h)

    def test_table_recognizes_separator_row(self) -> None:
        """无 --- 分隔行的 | 开头行不是表格（按段落渲染，防误判）。"""
        h = report._md_to_html("| 不是表格 |\n| 也不是 |")
        self.assertNotIn("<table>", h)
        self.assertIn("<p>| 不是表格 |</p>", h)

    def test_toc_nav_generated_with_anchors(self) -> None:
        """UX：有 h2 小节的报告 → 文首 <nav class="toc"> 目录 + 每个 h2 带 id 锚点。"""
        md = "# 标题\n\n## 甲节\n\n内容\n\n## 乙节\n\n内容\n"
        h = report._md_to_html(md)
        self.assertIn('<nav class="toc">', h)
        self.assertIn('href="#sec-0"', h)
        self.assertIn('href="#sec-1"', h)
        self.assertIn('<h2 id="sec-0">甲节</h2>', h)
        self.assertIn('<h2 id="sec-1">乙节</h2>', h)
        # 目录在正文最前（h1 之前或之后都行，但必须先于第一个 h2 出现）
        self.assertLess(h.index('<nav class="toc">'), h.index("<h2"))

    def test_toc_anchors_match_h2_sequence(self) -> None:
        """目录链接与 h2 id 一一对应（两遍推导不漂移）。"""
        md = "# T\n\n## A\n\n## B\n\n## C\n"
        h = report._md_to_html(md)
        for i, t in enumerate("ABC"):
            self.assertIn(f'<a href="#sec-{i}">{t}</a>', h)
            self.assertIn(f'<h2 id="sec-{i}">{t}</h2>', h)

    def test_no_h2_no_toc(self) -> None:
        """无 h2 的报告不生成空目录。"""
        h = report._md_to_html("# 只有 h1\n\n段落\n")
        self.assertNotIn('<nav class="toc">', h)


class TestGenerate(ReportBase):
    def test_returns_paths_and_rowcount(self) -> None:
        out = report.generate(self.con, "2026-W42", self.out_dir)
        self.assertTrue(out["md"].endswith("weekly-2026-W42.md"))
        self.assertTrue(out["html"].endswith("weekly-2026-W42.html"))
        # R238：rows = 本周发表的文章数（两源并集按标题去重：mA/mB/mC）
        self.assertEqual(out["rows"], 3)
        self.assertTrue(os.path.exists(out["md"]) and os.path.exists(out["html"]))

    def test_md_content_calibration_and_top_ordering(self) -> None:
        out = report.generate(self.con, "2026-W42", self.out_dir)
        md = open(out["md"]).read()
        self.assertIn("口径说明", md)
        self.assertIn("按概念分组", md)
        self.assertIn("2026-10-12 ~ 2026-10-18", md)
        # R238：Top 表改用 article_detail（ad30 口径，含渠道构成）：mB(0.31) > mA(0.25)
        # mC 只有 datacube 源，且 ad30 优先去重后仍应出现
        self.assertLess(md.index("文章mB"), md.index("文章mA"))
        # 单篇渠道构成段
        self.assertIn("单篇渠道构成", md)
        self.assertIn("45.0", md)
        self.assertIn("25.0%", md)
        html = open(out["html"]).read()
        # 阶段 6 模板化：不再是 <pre> 裸转义，表格应为真 <table>
        self.assertNotIn("<pre>", html)
        self.assertIn("<table>", html)
        self.assertIn("<th>文章</th>", html)
        self.assertIn("口径说明", html)
        self.assertIn("<title>mpdt 周报 2026-W42</title>", html)
        # 行内元素：**加粗** → <strong>，`码` → <code>
        self.assertIn("<strong>", html)
        self.assertIn("<code>", html)

    def test_md_contains_wide_table_for_read_completion(self) -> None:
        out = report.generate(self.con, "2026-W42", self.out_dir)
        md = open(out["md"]).read()
        self.assertIn("跨 series 对照", md)
        # 完读率宽表：列头 dc7 + ad30 都出现
        self.assertIn("完读率(dc7·7天)", md)
        self.assertIn("完读率(ad30·30天)", md)
        self.assertIn("mA_1", md)
        self.assertIn("10.0%", md)
        self.assertIn("25.0%", md)
        self.assertIn("不可直接比", md)
        # 阅读量宽表：3 列（pl / dc7 / ad30）
        self.assertIn("阅读量(pl·0天)", md)
        self.assertIn("阅读量(dc7·7天)", md)
        self.assertIn("阅读量(ad30·30天)", md)
        # 返回里声明了对照的概念
        self.assertIn("read_completion", out["concepts_compared"])

    def test_md_folds_empty_concepts_into_summary(self) -> None:
        out = report.generate(self.con, "2026-W42", self.out_dir)
        md = open(out["md"]).read()
        # 无数据概念折叠成一行（带"缺字段 X"诊断：fixture 没存 like_cnt/comment_cnt/share_uv）
        self.assertIn("本周无可对照数据的概念", md)
        # 缺字段 article_detail 字段：点赞/评论/分享
        self.assertIn("缺字段", md)
        self.assertIn("article_detail", md)
        # 折叠的概念名应该列出
        self.assertIn("点赞", md)
        # 折叠的概念列表在 concepts_empty 里
        self.assertIn("like", out["concepts_empty"])
        self.assertIn("comment", out["concepts_empty"])

    def test_all_zero_rows_folded_as_no_signal(self) -> None:
        # 整张宽表全 0 → 折叠成「无信号」汇总
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        # pl 全 0，ad30 全 0 → like 概念全 0
        store.upsert(
            con, "publish_list", "2026-10-16", "1", 1, "甲", {"like_num": 0, "comment_num": 0}
        )
        store.upsert(
            con, "article_detail", "2026-10-17", "1", 1, "甲", {"like_cnt": 0, "comment_cnt": 0}
        )
        con.commit()
        out = report.generate(con, "2026-W42", tmp)
        md = open(out["md"]).read()
        # 全 0 的 like/comment 概念应被折叠到「无信号」汇总
        self.assertIn("无信号", md)
        # 不应再展示它们的宽表（避免噪音）
        self.assertNotIn("### 概念「点赞」", md)
        self.assertNotIn("### 概念「评论」", md)
        # 但 like/comment 仍记在 folded 里
        self.assertIn("like", out["concepts_empty"])
        self.assertIn("comment", out["concepts_empty"])

    def test_md_no_pairwise_block(self) -> None:
        # 回归保护：周报里不应再出现旧式「series `a` vs `b`」小节（每概念一张宽表）
        out = report.generate(self.con, "2026-W42", self.out_dir)
        md = open(out["md"]).read()
        self.assertNotIn("series `pl` vs `dc7`", md)
        self.assertNotIn("series `pl` vs `ad30`", md)

    def test_empty_week_still_generates_report(self) -> None:
        out = report.generate(self.con, "2026-W20", self.out_dir)
        self.assertEqual(out["rows"], 0)
        md = open(out["md"]).read()
        self.assertIn("本周无 datacube 快照数据", md)
        self.assertIn("口径说明", md)

    def test_outside_week_rows_excluded(self) -> None:
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-19",
            "mX",
            1,
            "周外文章",
            {"finish_read_rate": 0.99, "total_pv": 1},
        )
        self.con.commit()
        out = report.generate(self.con, "2026-W42", self.out_dir)
        # R238：rows = 本周发表的文章数（两源并集按标题去重：mA/mB/mC）
        self.assertEqual(out["rows"], 3)
        md = open(out["md"]).read()
        self.assertNotIn("mX", md)


class TestHeaderUnitSemantics(unittest.TestCase):
    """ratio 概念宽表=「差值(百分点·两两差)」列；计数概念宽表无差值列。"""

    def test_count_concept_header_has_no_diff_column(self) -> None:
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        store.upsert(
            con,
            "publish_list",
            "2026-10-16",
            "999",
            1,
            "甲",
            {"read_num": 96},
            fetched_at="2026-10-17T09:00:00",
        )
        store.upsert(
            con,
            "datacube_article",
            "2026-10-16",
            "999",
            1,
            "甲",
            {"total_pv": 208},
            fetched_at="2026-10-17T09:00:00",
        )
        con.commit()
        for t in report.cross_source_table(con, "read_volume"):
            for h in t["header"]:
                self.assertNotIn("百分点", h)
                self.assertNotIn("两两差", h)
        for t in report.cross_source_table(con, "read_completion"):
            self.assertIn("差值(百分点·两两差)", t["header"])


class TestFoldingBehavior(unittest.TestCase):
    """空数据折叠：无数据行的概念合并成一行汇总。"""

    def test_no_data_concepts_listed_in_summary(self) -> None:
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        # 只造一篇 datacube，没有 article_detail / publish_list
        store.upsert(
            con,
            "datacube_article",
            "2026-10-15",
            "1",
            1,
            "甲",
            {"finish_read_rate": 0.3, "total_pv": 100},
        )
        con.commit()
        out = report.generate(con, "2026-W42", tmp)
        md = open(out["md"]).read()
        # 折叠汇总行
        self.assertIn("本周无可对照数据的概念", md)
        # 完读率也无数据（ad30 不存在）
        self.assertIn("完读率", md)
        # concepts_compared 为空
        self.assertEqual(out["concepts_compared"], [])
        # 但 concepts_empty 应含完读率
        self.assertIn("read_completion", out["concepts_empty"])

    def test_all_concepts_have_data_no_folding(self) -> None:
        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        # 同时给 pl + dc7 → read_volume 完整
        store.upsert(
            con,
            "publish_list",
            "2026-10-16",
            "1",
            1,
            "甲",
            {"read_num": 80, "share_num": 1, "like_num": 1, "old_like_num": 1, "comment_num": 0},
        )
        store.upsert(
            con,
            "datacube_article",
            "2026-10-15",
            "1",
            1,
            "甲",
            {
                "finish_read_rate": 0.3,
                "total_pv": 100,
                "total_share": 5,
                "send_succ": 50,
                "total_read_finish": 30,
                "read_rate": 0.5,
                "refdate": "2026-10-15",
            },
        )
        store.upsert(
            con,
            "article_detail",
            "2026-10-17",
            "1",
            1,
            "甲",
            {
                "read_uv": 200,
                "finished_read_pv_ratio": 0.25,
                "avg_article_read_time": 12,
                "share_uv": 8,
                "like_cnt": 3,
                "comment_cnt": 1,
                "collection_uv": 1,
                "listen_uv": 5,
                "listen_pv": 7,
                "zaikan_cnt": 2,
                "praise_money": 0,
                "follow_after_read_uv": 0,
                "publish_date": "2026-10-15",
            },
        )
        con.commit()
        out = report.generate(con, "2026-W42", tmp)
        open(out["md"]).read()
        # 所有 ≥2 series 概念都有数据
        for c in ("read_completion", "read_volume", "share", "like", "comment"):
            self.assertIn(c, out["concepts_compared"])
        # 折叠行不应出现（或为空）
        # 当所有多 series 概念都有数据时 concepts_empty 为空
        self.assertEqual(out["concepts_empty"], [])


class TestFakePerfectFinishRate(unittest.TestCase):
    """R237：微信在「无人读完」时把 finish_read_rate 返回 1（100%）而不是空值。

    客户看到「完读率 100%」会立刻质疑，而那是分母为 0。必须排除出 Top 排名。
    """

    def _run(self, rows: Any) -> Any:
        tmpdir = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmpdir, "t.sqlite3"))
        for sd, am, fr, tf in rows:
            store.upsert(
                con,
                "datacube_article",
                sd,
                am,
                1,
                "文章" + am,
                {
                    "refdate": sd.replace("-", ""),
                    "finish_read_rate": fr,
                    "read_rate": 0.5,
                    "total_pv": 30,
                    "total_share": 0,
                    "total_read_finish": tf,
                },
            )
        with tempfile.TemporaryDirectory() as d:
            out = report.generate(con, "2026-W43", d)
            md = open(out["md"], encoding="utf-8").read()
        return md

    def test_fake_100pct_excluded_from_top(self) -> None:
        md = self._run(
            [
                ("2026-10-20", "fake", 1.0, 0),  # 分母 0 的伪 100%
                ("2026-10-20", "real", 0.42, 12),  # 真实 42%
            ]
        )
        self.assertNotIn("文章fake", md.split("## 跨 series")[0])
        self.assertIn("文章real", md.split("## 跨 series")[0])
        self.assertIn("伪 100%", md)

    def test_note_reports_count(self) -> None:
        md = self._run([("2026-10-20", "fake", 1.0, 0), ("2026-10-20", "real", 0.42, 12)])
        self.assertIn("已排除 1 篇", md)

    def test_real_100pct_kept_when_finish_count_nonzero(self) -> None:
        # 分母不为 0 的真 100%（真的每人都读完）必须保留
        md = self._run([("2026-10-20", "perfect", 1.0, 9), ("2026-10-20", "normal", 0.42, 12)])
        top = md.split("## 跨 series")[0]
        self.assertIn("文章perfect", top)
        self.assertNotIn("已排除", md)

    def test_no_fake_no_note(self) -> None:
        md = self._run([("2026-10-20", "a", 0.42, 12), ("2026-10-20", "b", 0.30, 9)])
        self.assertNotIn("伪 100%", md)


class TestWeekSemanticsIsPublishDateNotFetchDate(unittest.TestCase):
    """R238 核心回归：周的语义 = 本周**发表**的文章，不是本周**抓到**的快照。

    真实事故：W43(10-19~10-25) 周报按 stat_date（抓取日）筛，
    datacube 命中 5 行里发表日在窗口内的 **0 行** —— 全是上周文章在今天被补抓。
    客户拿到会立刻问「这都是上周的文章吧」。
    """

    def _mk(self) -> Any:
        tmp = tempfile.mkdtemp()
        return store.connect(os.path.join(tmp, "t.sqlite3"))

    def test_article_published_in_week_but_fetched_later_is_included(self) -> None:
        con = self._mk()
        # 10-20 发表，但 10-22 才补抓 → 必须进 W43
        store.upsert(
            con,
            "article_detail",
            "2026-10-22",
            "p1",
            1,
            "本周发的",
            {
                "publish_date": "2026-10-20",
                "read_uv": 50,
                "finished_read_pv_ratio": 0.30,
                "avg_article_read_time": 20,
            },
        )
        con.commit()
        with tempfile.TemporaryDirectory() as d:
            md = open(report.generate(con, "2026-W43", d)["md"], encoding="utf-8").read()
        self.assertIn("本周发的", md)
        self.assertIn("**本周发表的文章**：**1** 篇", md)

    def test_article_published_last_week_fetched_this_week_is_excluded(self) -> None:
        con = self._mk()
        # 10-15 发表，本周（10-19~10-25）补抓 → 不该出现在 W43
        store.upsert(
            con,
            "article_detail",
            "2026-10-20",
            "p2",
            1,
            "上周发的",
            {
                "publish_date": "2026-10-15",
                "read_uv": 50,
                "finished_read_pv_ratio": 0.99,
                "avg_article_read_time": 20,
            },
        )
        con.commit()
        with tempfile.TemporaryDirectory() as d:
            md = open(report.generate(con, "2026-W43", d)["md"], encoding="utf-8").read()
        self.assertNotIn("上周发的", md)
        self.assertIn("**本周发表的文章**：**0** 篇", md)

    def test_daily_snapshots_dedupe_to_one_article(self) -> None:
        con = self._mk()
        for sd in ("2026-10-20", "2026-10-21", "2026-10-22"):
            store.upsert(
                con,
                "article_detail",
                sd,
                "p3",
                1,
                "同一篇",
                {
                    "publish_date": "2026-10-19",
                    "read_uv": 60,
                    "finished_read_pv_ratio": 0.25,
                    "avg_article_read_time": 15,
                },
            )
        con.commit()
        with tempfile.TemporaryDirectory() as d:
            md = open(report.generate(con, "2026-W43", d)["md"], encoding="utf-8").read()
        self.assertIn("**本周发表的文章**：**1** 篇", md)
        # Top 表里同一篇只能出现一次
        top = md.split("## 完读率 Top")[1].split("## 跨 series")[0]
        self.assertEqual(top.count("同一篇"), 1)

    def test_no_publish_date_falls_back_to_fetch_date(self) -> None:
        con = self._mk()
        # 无 publish_date / refdate 的老快照 → 按 stat_date 兜底，不丢数据
        store.upsert(
            con,
            "article_detail",
            "2026-10-20",
            "p4",
            1,
            "老数据",
            {"read_uv": 10, "finished_read_pv_ratio": 0.2},
        )
        con.commit()
        with tempfile.TemporaryDirectory() as d:
            md = open(report.generate(con, "2026-W43", d)["md"], encoding="utf-8").read()
        self.assertIn("老数据", md)


if __name__ == "__main__":
    unittest.main()


class TestTopTablePercentFormat(unittest.TestCase):
    """Top 表比例列（完读率/阅读率）必须是百分号格式，与宽表/渠道表一致。"""

    def test_top_ratio_columns_formatted(self) -> None:
        import os
        import tempfile

        from mpdt import report as rep
        from mpdt import store

        tmp = tempfile.mkdtemp()
        con = store.connect(os.path.join(tmp, "t.sqlite3"))
        store.upsert(
            con,
            "datacube_article",
            "2026-10-13",
            "777",
            1,
            "甲文",
            {"finish_read_rate": 0.3684, "read_rate": 0.0653, "total_pv": 25, "total_share": 1},
            fetched_at="2026-10-14T09:00:00",
        )
        con.commit()
        out = rep.generate(con, "2026-W42", os.path.join(tmp, "r"))
        md = open(out["md"]).read()
        self.assertIn("36.8%", md)
        self.assertIn("6.5%", md)
        self.assertNotIn("0.3684", md)
