"""解析层（mpdt/parsing.py）的**失败路径**。

这些用例补的是「上游改版时会发生什么」——正常路径已由真实 fixture 覆盖
（tests/test_fetchers.py 用的是 2026-10-17 录制的页面）。但真正决定运维体验的是
异常分支：结构一变，工具必须报「上游异常 / 退出码 4」并带上原始片段，而不是
静默返回半个结果。

每个 raise 断言同时检查消息里的关键词 —— 只说「抛了异常」的话，把
「外层非 JSON」写成「结构变更」也没人会发现。
"""

from __future__ import annotations

import json
import unittest

from mpdt import parsing
from mpdt.errors import UpstreamError


class TestPublishListFailures(unittest.TestCase):
    def test_non_json_outer(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_publish_list("not json at all")
        self.assertIn("外层非 JSON", str(ctx.exception))

    def test_base_resp_ret_nonzero(self) -> None:
        raw = json.dumps({"base_resp": {"ret": 200003}, "publish_page": "{}"})
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_publish_list(raw)
        self.assertIn("base_resp.ret=200003", str(ctx.exception))

    def test_publish_page_not_json(self) -> None:
        raw = json.dumps({"base_resp": {"ret": 0}, "publish_page": "{{bad"})
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_publish_list(raw)
        self.assertIn("二次解码失败", str(ctx.exception))

    def test_publish_list_missing(self) -> None:
        raw = json.dumps({"base_resp": {"ret": 0}, "publish_page": json.dumps({"nope": 1})})
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_publish_list(raw)
        self.assertIn("publish_list[] 缺失", str(ctx.exception))

    def test_broken_entry_is_skipped_not_fatal(self) -> None:
        """单条 publish_info 坏了跳过（journal 里能看到），不能让整批失败。"""
        page = {
            "total_count": 2,
            "publish_count": 2,
            "publish_list": [
                {"publish_info": "{{broken"},
                {
                    "publish_info": json.dumps(
                        {
                            "publish_info": {"create_time": 1792145145},
                            "appmsg_info": [{"appmsgid": 1, "itemidx": 1, "title": "ok"}],
                        }
                    )
                },
            ],
        }
        out = parsing.parse_publish_list(
            json.dumps({"base_resp": {"ret": 0}, "publish_page": json.dumps(page)})
        )
        self.assertEqual(len(out["articles"]), 1)
        self.assertEqual(out["articles"][0]["title"], "ok")

    def test_deleted_article_is_dropped(self) -> None:
        page = {
            "total_count": 1,
            "publish_count": 1,
            "publish_list": [
                {
                    "publish_info": json.dumps(
                        {
                            "publish_info": {"create_time": 1792145145},
                            "appmsg_info": [
                                {"appmsgid": 1, "itemidx": 1, "title": "deleted", "is_deleted": 1},
                                {"appmsgid": 2, "itemidx": 1, "title": "alive"},
                            ],
                        }
                    )
                }
            ],
        }
        out = parsing.parse_publish_list(
            json.dumps({"base_resp": {"ret": 0}, "publish_page": json.dumps(page)})
        )
        self.assertEqual([a["appmsgid"] for a in out["articles"]], [2])

    def test_invalid_create_time_yields_empty_publish_date(self) -> None:
        self.assertEqual(parsing._publish_date(None), "")
        self.assertEqual(parsing._publish_date(0), "")
        self.assertEqual(parsing._publish_date(-5), "")


class TestDatacubeFailures(unittest.TestCase):
    def test_non_json(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_tmpl17("<html>")
        self.assertIn("非JSON", str(ctx.exception))

    def test_missing_envelope(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_tmpl17(json.dumps({"data": []}))
        self.assertIn("data/base_resp 缺失", str(ctx.exception))

    def test_row_structure_changed(self) -> None:
        raw = json.dumps({"base_resp": {}, "data": [{"no_rate": 1}]})
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_tmpl17(raw)
        self.assertIn("finish_read_rate 缺失", str(ctx.exception))

    def test_numeric_strings_are_coerced(self) -> None:
        raw = json.dumps(
            {
                "base_resp": {},
                "data": [
                    {"finish_read_rate": "'0.1667'", "total_pv": "'1200'", "refdate": "2026-10-16"}
                ],
            }
        )
        row = parsing.parse_tmpl17(raw)[0]
        self.assertAlmostEqual(row["finish_read_rate"], 0.1667)
        self.assertEqual(row["total_pv"], 1200)
        self.assertIsInstance(row["total_pv"], int)
        self.assertEqual(row["refdate"], "2026-10-16")  # 日期字段不动

    def test_non_numeric_string_is_left_alone(self) -> None:
        """上游给非数值时不要猜：**原值**保留（连引号一起），让异常在手里可见。

        转数值失败时 out[k] 根本没被赋值 —— 这是刻意的：把 "'n/a'" 悄悄改成
        "n/a" 会让下游以为拿到了一个清洗过的值，而其实上游已经变了。
        """
        raw = json.dumps({"base_resp": {}, "data": [{"finish_read_rate": "'n/a'"}]})
        self.assertEqual(parsing.parse_tmpl17(raw)[0]["finish_read_rate"], "'n/a'")


class TestBraceMatching(unittest.TestCase):
    """JSON 提取必须感知字符串与转义：微信返回的标题/正文里全是引号和大括号。"""

    def test_braces_inside_strings_do_not_count(self) -> None:
        text = 'x = {"title": "a{b}c", "n": 1}'
        start = text.index("{")
        end = parsing._match_brace(text, start)
        self.assertEqual(text[start : end + 1], '{"title": "a{b}c", "n": 1}')

    def test_escaped_quote_does_not_close_the_string(self) -> None:
        text = '{"t": "he said \\"hi\\"}", "n": 2}'
        start = 0
        end = parsing._match_brace(text, start)
        self.assertEqual(end, len(text) - 1)
        json.loads(text[start : end + 1])  # 提取出来的必须是合法 JSON

    def test_unbalanced_returns_none(self) -> None:
        self.assertIsNone(parsing._match_brace('{"a": 1', 0))

    def test_json_after_missing_key_or_brace(self) -> None:
        self.assertIsNone(parsing._json_after("no such key", "articleData"))
        self.assertIsNone(parsing._json_after("articleData: 42", "articleData"))

    def test_json_after_returns_the_object_text(self) -> None:
        seg = parsing._json_after('var articleData: {"a": {"b": 1}};', "articleData")
        self.assertEqual(json.loads(seg or "null"), {"a": {"b": 1}})


class TestDetailFailures(unittest.TestCase):
    def test_empty_response(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|")
        self.assertIn("返回为空", str(ctx.exception))

    def test_non_200_status(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("302|<html>login</html>")
        self.assertIn("HTTP 302", str(ctx.exception))

    def test_missing_article_data(self) -> None:
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|<html>no data here</html>")
        self.assertIn("未找到 articleData", str(ctx.exception))

    def test_missing_one_of_three_parts(self) -> None:
        html = 'var articleData: {"msgid": "1_1"}; var detailData: {};'
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|" + html)
        self.assertIn("articleSummaryData 缺失", str(ctx.exception))

    def test_part_not_json(self) -> None:
        html = "var articleData: {bad json};var articleSummaryData: {};var detailData: {};"
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|" + html)
        self.assertIn("非 JSON", str(ctx.exception))

    def test_bad_msgid(self) -> None:
        html = 'var articleData: {"msgid": "nounderscore"};var articleSummaryData: {};var detailData: {};'
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|" + html)
        self.assertIn("msgid 缺失/异常", str(ctx.exception))

    def test_missing_article_data_new(self) -> None:
        html = 'var articleData: {"msgid": "2247485219_1"};var articleSummaryData: {};var detailData: {};'
        with self.assertRaises(UpstreamError) as ctx:
            parsing.parse_detail("200|" + html)
        self.assertIn("article_data_new 缺失", str(ctx.exception))


class TestChannelRows(unittest.TestCase):
    def test_rows_without_scene_are_skipped(self) -> None:
        rows = [{"read_user": 1}, {"scene": 6, "read_user": 10, "ref_date": "2026-10-16"}]
        out = parsing._channel_rows(rows)
        self.assertEqual([r["scene"] for r in out], [6])
        self.assertEqual(out[0]["scene_label"], "推荐")

    def test_unknown_scene_keeps_a_readable_label(self) -> None:
        out = parsing._channel_rows([{"scene": 42, "read_user": 1}])
        self.assertEqual(out[0]["scene_label"], "未知(42)")


if __name__ == "__main__":
    unittest.main()
