"""fetchers 的壳层：只验「取数 → 交给解析」这条线，不碰网络。

壳很薄，但薄不代表不用测：它的价值在于**调用顺序**——
先 ensure_token（可能要刷 token），再带上 token 发请求。
顺序反了就是一次 401，而 401 在日志里长得和「接口改版」很像。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from mpdt.fetchers import article_detail, datacube, publish_list


class _Session:
    """替身：记录被发出的 JS 与 token 调用次数。"""

    fingerprint = "fp-123"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.sent: list[str] = []
        self.token_calls = 0

    def ensure_token(self) -> str:
        self.token_calls += 1
        return "TOKEN-XYZ"

    def fetch_text(self, js: str) -> str:
        self.sent.append(js)
        return self.reply


def _publish_payload() -> str:
    page = {
        "total_count": 1,
        "publish_count": 1,
        "publish_list": [
            {
                "publish_info": json.dumps(
                    {
                        "publish_info": {"create_time": 1792145145},
                        "appmsg_info": [{"appmsgid": 1, "itemidx": 1, "title": "ok"}],
                    }
                )
            }
        ],
    }
    return json.dumps({"base_resp": {"ret": 0}, "publish_page": json.dumps(page)})


def _datacube_payload() -> str:
    return json.dumps(
        {
            "base_resp": {},
            "data": [
                {
                    "appmsgid": 1,
                    "refdate": "2026-10-16",
                    "finish_read_rate": "'0.5'",
                    "total_pv": "'7'",
                }
            ],
        }
    )


def _detail_payload() -> str:
    html = (
        'var articleData: {"msgid": "2247485219_1", "title": "t", "article_data_new": {"read_uv": 5}};'
        'var articleSummaryData: {"list": [{"scene": 9999, "read_user": 5}]};'
        "var detailData: {};"
    )
    return "200|" + html


class TestPublishListFetch(unittest.TestCase):
    def test_ask_for_token_first_then_send_request(self) -> None:
        s = _Session(_publish_payload())
        out = publish_list.fetch(s, count=5)
        self.assertEqual(s.token_calls, 1)
        self.assertIn("count=5", s.sent[0])
        self.assertIn("TOKEN-XYZ", s.sent[0])
        self.assertEqual(len(out["articles"]), 1)

    def test_empty_page_with_nonzero_total_is_a_contract_violation(self) -> None:
        """解析成功但一条都没有、上游却说有内容 ⇒ 拼接/结构出了问题，不能当成空数据。"""
        bad = {"articles": [], "total_publish": 5, "publish_count": 5}
        with (
            mock.patch.object(publish_list, "parse_publish_list", return_value=bad),
            self.assertRaises(AssertionError),
        ):
            publish_list.fetch(_Session("whatever"))

    def test_genuinely_empty_account_is_fine(self) -> None:
        empty = {"articles": [], "total_publish": 0, "publish_count": 0}
        with mock.patch.object(publish_list, "parse_publish_list", return_value=empty):
            out = publish_list.fetch(_Session("whatever"))
        self.assertEqual(out["articles"], [])


class TestDatacubeFetch(unittest.TestCase):
    def test_range_and_fingerprint_reach_the_request(self) -> None:
        s = _Session(_datacube_payload())
        rows = datacube.fetch(s, "20261010", "20261016", size=7)
        js = s.sent[0]
        self.assertIn("20261010", js)
        self.assertIn("20261016", js)
        self.assertIn("size:7", js)
        self.assertIn("fp-123", js)
        self.assertEqual(rows[0]["total_pv"], 7)


class TestArticleDetailFetch(unittest.TestCase):
    def test_ids_and_publish_date_reach_the_request(self) -> None:
        s = _Session(_detail_payload())
        d = article_detail.fetch(s, "2247485219", 1, "2026-10-16")
        js = s.sent[0]
        self.assertIn("msgid=2247485219_1", js)
        self.assertIn("publish_date=2026-10-16", js)
        self.assertEqual(d["appmsgid"], "2247485219")

    def test_itemidx_is_coerced_to_int_in_the_url(self) -> None:
        """URL 里的下标不能带引号：字符串 "2" 会被接口当成另一种类型。"""
        s = _Session(_detail_payload())
        article_detail.fetch(s, "1", 2, "2026-10-16")
        self.assertIn("_2&", s.sent[0])


class TestReExportsStillWork(unittest.TestCase):
    """旧导入路径必须继续有效 —— 拆文件不该让调用方改一行代码。"""

    def test_parse_functions_are_reachable_from_fetcher_modules(self) -> None:
        self.assertTrue(callable(publish_list.parse_publish_list))
        self.assertTrue(callable(datacube.parse_tmpl17))
        self.assertTrue(callable(article_detail.parse_detail))
        self.assertTrue(callable(article_detail.channel_breakdown))
        self.assertEqual(article_detail.ALL_SCENE, 9999)


if __name__ == "__main__":
    unittest.main()
