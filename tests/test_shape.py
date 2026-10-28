"""整形层（mpdt/shape.py）：数据 → 数据，以及少数「数据 → 判定」。

这一层就是口径本身。所以这里的断言写的是**规则**，不是实现细节：

- 哪些文章算「最近 N 天」（发表日缺失怎么办、重复怎么办）
- 完读率的分母、渠道占比的分母、`total_read_user` 只数「全部」那一行
- 导出列序为什么排序而不是按出现顺序
- IMAP 的主题为什么要按 RFC2047 解码（微信/QQ 的主题是 Base64 编码的）

改这里等于改客户看到的数字，所以每条规则配一个能说清「为什么」的用例名。
"""

from __future__ import annotations

import datetime as dt
import unittest
from email.header import Header
from typing import Any, ClassVar

from mpdt import shape


class TestSelectRecentArticles(unittest.TestCase):
    def test_keeps_only_window_and_dedups(self) -> None:
        articles = [
            {"appmsgid": 1, "itemidx": 1, "publish_date": "2026-10-14"},
            {"appmsgid": 1, "itemidx": 1, "publish_date": "2026-10-14"},  # 同一篇出现两次
            {"appmsgid": 2, "itemidx": 1, "publish_date": "2026-10-01"},  # 早于 cutoff
            {"appmsgid": 4, "itemidx": 2, "publish_date": "2026-10-12"},
        ]
        got = shape.select_recent_articles(articles, "2026-10-05")
        self.assertEqual(got, [("1", 1, "2026-10-14"), ("4", 2, "2026-10-12")])

    def test_article_without_publish_date_is_dropped(self) -> None:
        """没有发表日就没法构造渠道页 URL（publish_date 是必填参数）。"""
        articles = [{"appmsgid": 3, "itemidx": 1, "publish_date": ""}]
        self.assertEqual(shape.select_recent_articles(articles, "2026-10-01"), [])

    def test_input_order_is_preserved(self) -> None:
        """发表列表是「最新在前」，重排会让 --recent 抓到的最先是最旧的。"""
        articles = [
            {"appmsgid": 9, "itemidx": 1, "publish_date": "2026-10-16"},
            {"appmsgid": 8, "itemidx": 1, "publish_date": "2026-10-15"},
        ]
        got = shape.select_recent_articles(articles, "2026-10-01")
        self.assertEqual([g[0] for g in got], ["9", "8"])


class TestDetailMetrics(unittest.TestCase):
    def _detail(self, channels: list[dict[str, int]]) -> dict[str, Any]:
        return {
            "metrics": {"read_uv": 100, "finished_read_pv_ratio": 0.25},
            "publish_date": "2026-10-12",
            "window_days": 30,
            "channels": channels,
            "channel_breakdown": [{"scene_label": "推荐", "read_pct": 45.0}],
        }

    def test_total_read_user_counts_only_the_all_scene_row(self) -> None:
        """各渠道会重复计人（同一个人从推荐和消息都能进），所以分母只能是「全部」。"""
        d = self._detail(
            [
                {"scene": 9999, "read_user": 60},
                {"scene": 6, "read_user": 30},
                {"scene": 7, "read_user": 10},
            ]
        )
        m = shape.detail_metrics(d, all_scene=9999)
        self.assertEqual(m["total_read_user"], 60)

    def test_derived_columns_are_added(self) -> None:
        m = shape.detail_metrics(self._detail([{"scene": 9999, "read_user": 0}]), all_scene=9999)
        self.assertEqual(m["window_days"], 30)
        self.assertEqual(m["publish_date"], "2026-10-12")
        self.assertIn("channel_breakdown", m)
        self.assertEqual(m["read_uv"], 100)  # 原始指标不被覆盖


class TestDetailView(unittest.TestCase):
    def test_summary_and_line_come_from_the_same_numbers(self) -> None:
        d = {
            "appmsgid": "2247485219",
            "itemidx": 1,
            "title": "文章A",
            "publish_date": "2026-10-12",
            "channel_breakdown": [
                {"scene_label": "推荐", "read_pct": 45.0},
                {"scene_label": "公众号消息", "read_pct": 30.0},
            ],
        }
        summary, line = shape.detail_view(d, {"read_uv": 100, "finished_read_pv_ratio": 0.25})
        self.assertEqual(summary["recommend_pct"], 45.0)
        self.assertIn("25.0%", line)
        self.assertIn("45.0%", line)
        self.assertEqual(summary["title"], "文章A")  # 标题在摘要里，人读行只放数字

    def test_missing_recommend_channel_reports_none_not_zero(self) -> None:
        """「没有推荐」和「推荐是 0%」是两件事，前者不能显示成 0%。"""
        d = {
            "appmsgid": "1",
            "itemidx": 1,
            "title": "t",
            "publish_date": "2026-10-12",
            "channel_breakdown": [{"scene_label": "公众号消息", "read_pct": 100.0}],
        }
        summary, _ = shape.detail_view(d, {"read_uv": 10, "finished_read_pv_ratio": None})
        self.assertIsNone(summary["recommend_pct"])


class TestExportShapes(unittest.TestCase):
    ROWS: ClassVar[list[tuple[str, str, int, str, str]]] = [
        ("2026-10-12", "1", 1, "A", '{"b":1,"a":2}'),
        ("2026-10-13", "1", 1, "A", '{"c":3,"a":4}'),
    ]

    def test_columns_are_sorted_and_order_independent(self) -> None:
        """列序必须与行序无关：否则同一份数据两次导出得到不同文件，diff 全废。"""
        self.assertEqual(shape.export_columns(self.ROWS), ["a", "b", "c"])
        self.assertEqual(shape.export_columns(list(reversed(self.ROWS))), ["a", "b", "c"])

    def test_csv_matrix_fills_missing_cells_with_empty_string(self) -> None:
        cols = shape.export_columns(self.ROWS)
        matrix = shape.export_matrix(self.ROWS, cols)
        self.assertEqual(matrix[0], ["2026-10-12", "1", 1, "A", 2, 1, ""])

    def test_json_records_keep_types_and_drop_missing_keys(self) -> None:
        cols = shape.export_columns(self.ROWS)
        recs = shape.export_records(self.ROWS, cols)
        self.assertEqual(recs[0]["a"], 2)
        self.assertNotIn("c", recs[0])  # CSV 补空串，JSON 不编键
        self.assertEqual(recs[1]["c"], 3)

    def test_payload_shape(self) -> None:
        cols = ["a"]
        recs = shape.export_records(self.ROWS, cols)
        payload = shape.export_payload("article_detail", cols, recs, "2026-10-17T00:00:00+08:00")
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["columns"], ["stat_date", "appmsgid", "itemidx", "title", "a"])
        self.assertNotIn("metrics_meta", payload)
        with_meta = shape.export_payload(
            "article_detail", cols, recs, "2026-10-17T00:00:00+08:00", {"rows": []}
        )
        self.assertIn("metrics_meta", with_meta)


class TestAdviceRows(unittest.TestCase):
    RAW: ClassVar[list[tuple[str, str, str, str, int]]] = [
        (
            "2026-10-12",
            "文章A",
            '{"read_uv":10,"publish_date":"2026-10-12",'
            '"channel_breakdown":[{"scene_label":"推荐","read_user":3}]}',
            "1",
            1,
        ),
        (
            "2026-10-13",
            "文章A",
            '{"read_uv":11,"publish_date":"2026-10-12",'
            '"channel_breakdown":[{"scene_label":"推荐","read_user":9}]}',
            "1",
            1,
        ),
        ("2026-10-12", "文章B", '{"read_uv":0,"publish_date":"2026-10-11"}', "2", 1),
    ]

    def test_latest_snapshot_per_article_wins(self) -> None:
        """早期快照在新文章还没数据时是 read_uv=0，取早的会把好文章误判成「没量」。"""
        rows = shape.to_advice_rows(self.RAW)
        a = next(r for r in rows if r["title"] == "文章A")
        self.assertEqual(a["read"], 11)
        self.assertEqual(a["rec"], 9)

    def test_rows_without_numeric_read_are_dropped(self) -> None:
        raw = [("2026-10-12", "X", '{"read_uv":null,"publish_date":"2026-10-12"}', "3", 1)]
        self.assertEqual(shape.to_advice_rows(raw), [])

    def test_only_dates_filter(self) -> None:
        rows = shape.to_advice_rows(self.RAW, only_dates={"2026-10-11"})
        self.assertEqual([r["title"] for r in rows], ["文章B"])

    def test_sorted_by_date_then_recommend_desc(self) -> None:
        rows = shape.to_advice_rows(self.RAW)
        self.assertEqual([r["date"] for r in rows], sorted(r["date"] or "" for r in rows))


class TestFetchWindow(unittest.TestCase):
    TODAY = dt.date(2026, 10, 17)

    def test_default_is_yesterday_only(self) -> None:
        begin, end = shape.fetch_window(self.TODAY, None, None, None)
        self.assertEqual((begin, end), (dt.date(2026, 10, 16), dt.date(2026, 10, 16)))

    def test_backfill_n_days(self) -> None:
        begin, _end = shape.fetch_window(self.TODAY, 7, None, None)
        self.assertEqual(begin, dt.date(2026, 10, 10))

    def test_explicit_range_overrides(self) -> None:
        begin, end = shape.fetch_window(self.TODAY, 7, "2026-09-01", "2026-09-30")
        self.assertEqual((begin.isoformat(), end.isoformat()), ("2026-09-01", "2026-09-30"))


class TestPublishRows(unittest.TestCase):
    def test_send_hour_is_beijing_time(self) -> None:
        # 1792145145 = 2026-10-16 18:05:45 +08
        self.assertEqual(shape.send_hour_of(1792145145), "18:05")

    def test_missing_create_time_yields_none_not_a_guess(self) -> None:
        self.assertIsNone(shape.send_hour_of(None))
        self.assertIsNone(shape.send_hour_of(0))
        self.assertIsNone(shape.send_hour_of("1792145145"))  # 字符串不当时间戳

    def test_publish_metrics_carry_all_five_counters(self) -> None:
        a = {
            "read_num": 1,
            "share_num": 2,
            "like_num": 3,
            "old_like_num": 4,
            "comment_num": 5,
            "create_time": 1792145145,
            "publish_date": "2026-10-16",
        }
        m = shape.publish_list_metrics(a)
        for k in shape.PUBLISH_METRIC_KEYS:
            self.assertIn(k, m)
        self.assertEqual(m["send_hour"], "18:05")
        self.assertEqual(m["create_time"], 1792145145)

    def test_missing_create_time_nulls_all_three_time_fields(self) -> None:
        """上游没给 create_time 时三个时间字段一起为 null —— 不虚构。"""
        m = shape.publish_list_metrics({"title": "x"})
        self.assertIsNone(m["create_time"])
        self.assertIsNone(m["send_hour"])
        self.assertIsNone(m["publish_date"])

    def test_datacube_metrics_keep_only_known_keys(self) -> None:
        m = shape.datacube_metrics({**dict.fromkeys(shape.DATACUBE_METRIC_KEYS, 1), "extra": 9})
        self.assertNotIn("extra", m)
        self.assertEqual(set(m), set(shape.DATACUBE_METRIC_KEYS))


class TestMailHeaders(unittest.TestCase):
    """IMAP 回查靠主题匹配 —— 主题是 RFC2047 编码的，不解码就永远匹配不上。"""

    def test_plain_subject(self) -> None:
        raw = b"Subject: hello\r\nFrom: a@b.c\r\n\r\n"
        self.assertEqual(shape.mail_subject(raw), "hello")

    def test_base64_encoded_subject_is_decoded(self) -> None:
        encoded = Header("公众号数据周报 2026-W43", "utf-8").encode()
        raw = f"Subject: {encoded}\r\n\r\n".encode()
        self.assertEqual(shape.mail_subject(raw), "公众号数据周报 2026-W43")

    def test_str_input_is_accepted(self) -> None:
        self.assertEqual(shape.mail_subject("Subject: 纯文本\r\n\r\n"), "纯文本")

    def test_raw_utf8_subject_is_still_readable(self) -> None:
        """非 RFC2047 的原始 UTF-8 主题（部分客户端会这么写）不能被读成乱码 ——
        读成乱码的后果是把「已送达」判成未送达，然后重发一遍。"""
        raw = "Subject: 运维告警 2026-W43\r\n\r\n".encode()
        self.assertEqual(shape.mail_subject(raw), "运维告警 2026-W43")

    def test_invalid_utf8_falls_back_to_latin1(self) -> None:
        """unknown-8bit 但不是合法 UTF-8（老客户端 GBK/ISO-8859 混用）：
        退 latin-1 保住字节，至少不会抛异常炸掉整次回查。"""
        raw = b"Subject: caf\xe9 \xff\r\n\r\n"
        self.assertIn("caf", shape.mail_subject(raw))

    def test_unknown_charset_name_does_not_raise(self) -> None:
        raw = b"Subject: =?nosuchcharset?b?aGk=?=\r\n\r\n"
        self.assertEqual(shape.mail_subject(raw), "hi")

    def test_subject_hit_returns_position_of_first_match(self) -> None:
        self.assertEqual(shape.subject_hit(["a", "周报 W43", "b"], "W43"), (True, 2))
        self.assertEqual(shape.subject_hit(["a", "b"], "W43"), (False, 2))

    def test_empty_needle_never_matches(self) -> None:
        """空主题会让「什么都匹配」—— 那等于把未送达判成已送达。"""
        self.assertEqual(shape.subject_hit(["a", "b"], ""), (False, 2))


if __name__ == "__main__":
    unittest.main()
