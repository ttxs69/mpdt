"""metrics（概念分类树）单元测试：CONCEPTS / META_FIELDS / DEPRECATED 完整性；
series 查找 / 自动配对 / 文档生成。

运行：python3 tests/test_metrics.py 或 unittest discover  （零第三方依赖）
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import metrics  # noqa: E402


class TestConceptTreeIntegrity(unittest.TestCase):
    """CONCEPTS 结构完整性：每个概念必有 display/unit/series；每个 series 6 字段必填。
    META_FIELDS / DEPRECATED 同样结构化。"""

    def test_every_concept_has_required_shape(self) -> None:
        for cname, c in metrics.CONCEPTS.items():
            self.assertTrue(cname and c["display"] and c.get("unit"), cname)
            self.assertIn("series", c)
            self.assertTrue(c["series"], f"{cname} 至少一个 series")
            for skey, s in c["series"].items():
                for k in ("source", "field", "window", "window_days", "denominator"):
                    self.assertIn(k, s, f"{cname}.{skey} 缺 {k}")
                self.assertIn(
                    s["source"],
                    metrics.SOURCES,
                    "{}.{} 源 {} 不在 SOURCES".format(cname, skey, s["source"]),
                )

    def test_meta_fields_have_same_shape(self) -> None:
        for mname, m in metrics.META_FIELDS.items():
            self.assertTrue(mname and m["display"] and m.get("unit") and m.get("kind"), mname)
            for skey, s in m["series"].items():
                for k in ("source", "field", "window", "window_days", "denominator"):
                    self.assertIn(k, s, f"META.{mname}.{skey} 缺 {k}")

    def test_deprecated_has_field_and_replaced_by(self) -> None:
        for dname, d in metrics.DEPRECATED.items():
            self.assertTrue(dname and d.get("display") and d.get("replaced_by"), dname)
            for _skey, s in d["series"].items():
                self.assertIn("source", s)
                self.assertIn("field", s)

    def test_sources_cover_all_three(self) -> None:
        # 全注册表（CONCEPTS + META_FIELDS + DEPRECATED）三源都要有
        seen = set()
        for table in (metrics.CONCEPTS, metrics.META_FIELDS, metrics.DEPRECATED):
            for c in table.values():
                for s in c["series"].values():
                    seen.add(s["source"])
        self.assertEqual(seen, set(metrics.SOURCES))

    def test_field_unique_within_source_in_concepts(self) -> None:
        # CONCEPTS 内部同源 field 唯一（find_series 不歧义）
        seen = {}
        for _, _, s in metrics.all_series():
            key = (s["source"], s["field"])
            self.assertNotIn(key, seen, f"同源内 field 应唯一: {key} (另有 {seen.get(key)})")
            seen[key] = s

    def test_window_days_consistent_with_source(self) -> None:
        for _, _, s in metrics.all_series():
            wd = s["window_days"]
            if s["source"] == "publish_list":
                self.assertEqual(wd, 0)
            elif s["source"] == "datacube_article":
                self.assertEqual(wd, 7)
            elif s["source"] == "article_detail":
                self.assertEqual(wd, 30)

    def test_core_fields_registered(self) -> None:
        # 三源入库时实际写的键必须全部能 find_series 命中（CONCEPTS ∪ META ∪ DEPRECATED）
        dc = {
            "refdate",
            "finish_read_rate",
            "read_rate",
            "total_pv",
            "total_read_finish",
            "total_share",
            "send_succ",
        }
        pl = {"read_num", "share_num", "like_num", "old_like_num", "comment_num"}
        ad = set(__import__("mpdt.fetchers.article_detail", fromlist=["METRIC_KEYS"]).METRIC_KEYS)
        ad |= {"publish_date", "window_days", "total_read_user", "channel_breakdown"}
        for src, keys in (("datacube_article", dc), ("publish_list", pl), ("article_detail", ad)):
            for k in keys:
                self.assertIsNotNone(metrics.find_series(field=k, source=src), f"未登记: {src}.{k}")

    def test_metadata_fields_not_in_concepts(self) -> None:
        # 元数据字段（refdate/publish_date/window_days/total_read_user/channel_breakdown）移出 CONCEPTS
        for cname, c in metrics.CONCEPTS.items():
            for skey, s in c["series"].items():
                self.assertNotIn(
                    s["field"],
                    {
                        "refdate",
                        "publish_date",
                        "window_days",
                        "total_read_user",
                        "channel_breakdown",
                    },
                    f"{cname}.{skey} 是元数据字段，应在 META_FIELDS 而非 CONCEPTS",
                )

    def test_old_like_num_in_deprecated(self) -> None:
        # old_like_num 标 deprecated，保留登记但不在 CONCEPTS
        hit = metrics.find_series(field="old_like_num")
        self.assertEqual(hit[0], "old_like_num")
        # 不在 all_series 流水线（被 deprecated 排除）
        all_keys = [(c, k) for c, k, _ in metrics.all_series()]
        self.assertNotIn(("old_like_num", "pl2"), all_keys)

    def test_zaikan_cnt_is_its_own_concept(self) -> None:
        # zaikan_cnt 拆出独立概念「在看」(wow)，不再混入 like
        a = metrics.find_series(field="zaikan_cnt")
        self.assertEqual(a[0], "wow")
        like = metrics.find_series(field="like_cnt")
        self.assertEqual(like[0], "like")
        # 不再同概念
        self.assertNotEqual(a[0], like[0])

    def test_listen_split_into_two_concepts(self) -> None:
        # listen_uv/listen_pv 是两个不同概念，不是同概念两 series
        uv = metrics.find_series(field="listen_uv")
        pv = metrics.find_series(field="listen_pv")
        self.assertEqual(uv[0], "listen_people")
        self.assertEqual(pv[0], "listen_plays")
        self.assertNotEqual(uv[0], pv[0])

    def test_two_finish_read_rates_belong_to_same_concept(self) -> None:
        a = metrics.find_series(field="finish_read_rate")
        b = metrics.find_series(field="finished_read_pv_ratio")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[2]["window"], b[2]["window"])
        self.assertNotEqual(a[2]["denominator"], b[2]["denominator"])


class TestFindSeries(unittest.TestCase):
    def test_by_field_without_source(self) -> None:
        hit = metrics.find_series(field="read_num")
        self.assertIsNotNone(hit)
        cname, _skey, s = hit
        self.assertEqual(s["source"], "publish_list")
        self.assertEqual(cname, "read_volume")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(metrics.find_series(field="no_such_field"))

    def test_with_source_disambiguates(self) -> None:
        a = metrics.find_series(field="finish_read_rate", source="datacube_article")
        b = metrics.find_series(field="finished_read_pv_ratio", source="article_detail")
        self.assertEqual(a[1], "dc7")
        self.assertEqual(b[1], "ad30")

    def test_source_mismatch_returns_none(self) -> None:
        self.assertIsNone(metrics.find_series(field="read_num", source="article_detail"))

    def test_finds_meta_field(self) -> None:
        # 元数据字段也能 find_series
        hit = metrics.find_series(field="refdate", source="datacube_article")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], "refdate")

    def test_finds_deprecated_field(self) -> None:
        hit = metrics.find_series(field="old_like_num", source="publish_list")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], "old_like_num")


class TestToConceptsView(unittest.TestCase):
    def test_full_view_excludes_meta_and_deprecated(self) -> None:
        view = metrics.to_concepts_view()
        self.assertEqual(len(view), len(metrics.CONCEPTS))
        for c in view:
            self.assertIn("concept", c)
            self.assertIn("display", c)
            self.assertIn("series", c)
            for s in c["series"]:
                self.assertIn("key", s)
                self.assertIn("source", s)
                self.assertIn("field", s)
                self.assertIn("window", s)
                self.assertIn("window_days", s)
                self.assertIn("denominator", s)
        total_series = sum(len(c["series"]) for c in view)
        self.assertEqual(total_series, sum(1 for _ in metrics.all_series()))
        # 视图里不应出现元数据 / 旧版字段
        all_fields = [s["field"] for c in view for s in c["series"]]
        for meta in (
            "refdate",
            "publish_date",
            "window_days",
            "total_read_user",
            "channel_breakdown",
        ):
            self.assertNotIn(meta, all_fields, f"{meta} 是元数据字段，不应出现在 metrics 视图")
        # 旧版字段不在
        self.assertNotIn("old_like_num", all_fields)
        # zaikan_cnt 应在（属于 wow 概念）但不在 like 概念下
        self.assertIn("zaikan_cnt", all_fields)
        like = next(c for c in view if c["concept"] == "like")
        like_fields = {s["field"] for s in like["series"]}
        self.assertNotIn("zaikan_cnt", like_fields)
        self.assertNotIn("old_like_num", like_fields)

    def test_query_finds_concept_by_concept_name(self) -> None:
        view = metrics.to_concepts_view("完读率")
        concepts = {c["concept"] for c in view}
        self.assertIn("read_completion", concepts)

    def test_query_no_match_empty(self) -> None:
        self.assertEqual(metrics.to_concepts_view("zzz不存在"), [])

    def test_wow_is_listable(self) -> None:
        # 在「在看」查询时返回 wow
        view = metrics.to_concepts_view("在看")
        self.assertIn("wow", {c["concept"] for c in view})


class TestAutoPairs(unittest.TestCase):
    """易混淆配对自动推导：同概念 ≥2 series（非 deprecated）即一对；why 自动生成。"""

    def test_deprecated_excluded_from_pairs(self) -> None:
        # like 概念现在只有 pl+ad30 两个 series（old_like_num 已 deprecated）
        # 之前是 4 series → C(4,2)=6 对；现在只 1 对
        pairs = metrics.auto_pairs()
        like_pairs = [p for p in pairs if p[0] == "like"]
        self.assertEqual(len(like_pairs), 1, "like 概念 deprecated 后应只 1 对配对")
        # 4-tuple: (concept, a, b, why)
        self.assertEqual(like_pairs[0][1], "pl")
        self.assertEqual(like_pairs[0][2], "ad30")

    def test_listen_no_longer_paired(self) -> None:
        # listen 已拆成 listen_people + listen_plays 两个单 series 概念
        # 之前 listen 概念是 (ad30u, ad30p) 一对；现在没有 listen 概念
        pairs = metrics.auto_pairs()
        self.assertFalse(any(p[0] == "listen" for p in pairs))

    def test_at_least_one_pair_per_multi_series_concept(self) -> None:
        pairs = metrics.auto_pairs()
        concepts_with_pair = {p[0] for p in pairs}
        for cname, c in metrics.CONCEPTS.items():
            non_dep = [k for k, s in c["series"].items() if not s.get("deprecated")]
            if len(non_dep) >= 2:
                self.assertIn(
                    cname,
                    concepts_with_pair,
                    "概念 %s 有 %d series 但未生成配对" % (cname, len(non_dep)),
                )

    def test_no_pair_for_single_series_concept(self) -> None:
        for cname, _, _, _ in metrics.auto_pairs():
            non_dep = [
                k for k, s in metrics.CONCEPTS[cname]["series"].items() if not s.get("deprecated")
            ]
            self.assertGreaterEqual(len(non_dep), 2)

    def test_known_pair_finish_read_in_output(self) -> None:
        pairs = metrics.auto_pairs()
        self.assertTrue(
            any(p[0] == "read_completion" and p[1] == "dc7" and p[2] == "ad30" for p in pairs)
        )

    def test_why_mentions_window_or_denominator_diff(self) -> None:
        for cname, _a, _b, why in metrics.auto_pairs():
            if cname == "read_completion":
                self.assertIn("窗口", why)
                self.assertIn("分母", why)
                self.assertIn("不可直接比", why)

    def test_count_consistent_with_concept_combinations(self) -> None:
        from math import comb

        expected = sum(
            comb(len([k for k, s in c["series"].items() if not s.get("deprecated")]), 2)
            for c in metrics.CONCEPTS.values()
            if len([k for k, s in c["series"].items() if not s.get("deprecated")]) >= 2
        )
        self.assertEqual(len(metrics.auto_pairs()), expected)


class TestGeneratedDocs(unittest.TestCase):
    def test_calibration_note_mentions_concept_display(self) -> None:
        note = metrics.calibration_note()
        self.assertIn("口径说明", note)
        # 按概念分组：每个概念的 display 应在口径说明里
        for cname, c in metrics.CONCEPTS.items():
            self.assertIn(c["display"], note, f"概念 {cname} 缺口径说明")
        # 「不可直接比」统一在节首提一次，不再每概念行重复
        self.assertIn("不可直接比", note)

    def test_calibration_note_no_meta_or_deprecated(self) -> None:
        # 元数据字段 / 旧版字段不应作为「指标概念」出现在口径说明里
        # （footer 里提到它们的名字作为指引是允许的，但不应有完整 series 描述）
        note = metrics.calibration_note()
        # 没有「T+1 出数」直接出现于 meta 字段行（meta 字段不再列完整口径）
        self.assertNotIn("文章口径日", note)
        self.assertNotIn("派生列", note)
        self.assertNotIn("结构列", note)
        self.assertNotIn("旧版字段", note)
        # 旧版字段名不应在
        self.assertNotIn("old_like_num", note)
        # 「窗口 T+1」等只应该出现在指标概念行，不应该出现在 footer
        for line in note.splitlines():
            if "元数据字段" in line:
                # 这一行不应再展开字段窗口/分母
                self.assertNotIn("发表后", line)

    def test_calibration_note_compact_for_ratio_concepts(self) -> None:
        # 完读率概念一行（而不是按源列长串）
        note = metrics.calibration_note()
        # 完读率行应包含两个 series 的简短表述
        for line in note.splitlines():
            if "完读率" in line and "finish_read_rate" in line:
                # 同时出现两个字段
                self.assertIn("finished_read_pv_ratio", line)
                return
        self.fail("完读率行未同时含两个 series 字段名")

    def test_readme_section_grouped_by_concept(self) -> None:
        sec = metrics.readme_section()
        self.assertTrue(sec.startswith("## 指标口径"), sec[:50])
        for cname, c in metrics.CONCEPTS.items():
            self.assertIn("#### {}".format(c["display"]), sec)
            for skey in c["series"]:
                self.assertIn(f"`{skey}`", sec)
        # 元数据节、deprecated 节都要在
        self.assertIn("元数据字段", sec)
        self.assertIn("旧版字段", sec)
        # auto_pairs 在文档里
        self.assertIn("自动推导的易混淆配对", sec)
        for cname, _a, _b, _ in metrics.auto_pairs():
            self.assertIn(cname, sec)

    def test_readme_section_meta_table_has_all_meta_fields(self) -> None:
        sec = metrics.readme_section()
        for m in metrics.META_FIELDS:
            self.assertIn(f"`{m}`", sec)


if __name__ == "__main__":
    unittest.main()
