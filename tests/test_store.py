"""store 单元测试（T2）：connect 建库 / upsert 幂等 / log_run / export_csv。
运行：python3 tests/test_store.py  （unittest，零第三方依赖）
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


from mpdt import store  # noqa: E402


class StoreBase(unittest.TestCase):
    def setUp(self) -> Any:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "data", "mpdt.sqlite3")
        self.con = store.connect(self.db)

    def tearDown(self) -> Any:
        self.con.close()
        self._tmp.cleanup()

    def rows(self, table: Any = "snapshots") -> Any:
        return self.con.execute(f"SELECT * FROM {table}").fetchall()


class TestConnect(StoreBase):
    def test_creates_db_and_schema(self) -> None:
        self.assertTrue(os.path.exists(self.db))
        # 建库时自动创建 snapshots / journal 两表
        tables = {
            r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("snapshots", tables)
        self.assertIn("journal", tables)

    def test_reconnect_keeps_data(self) -> None:
        store.upsert(self.con, "publish_list", "2026-10-16", "m1", 1, "t", {"read_num": 1})
        self.con.commit()
        con2 = store.connect(self.db)  # 二次 connect 幂等（CREATE IF NOT EXISTS）
        self.assertEqual(len(con2.execute("SELECT * FROM snapshots").fetchall()), 1)
        con2.close()


class TestUpsertIdempotent(StoreBase):
    def test_same_pk_twice_keeps_single_row(self) -> None:
        store.upsert(
            self.con,
            "publish_list",
            "2026-10-16",
            "m1",
            1,
            "旧标题",
            {"read_num": 10, "share_num": 1},
        )
        store.upsert(
            self.con,
            "publish_list",
            "2026-10-16",
            "m1",
            1,
            "新标题",
            {"read_num": 20, "share_num": 2},
        )
        self.con.commit()
        rows = self.rows()
        self.assertEqual(len(rows), 1, "同主键重复 upsert 应只保留一行")
        title, metrics = rows[0][4], json.loads(rows[0][5])
        self.assertEqual(title, "新标题")  # 后写覆盖（取最新快照）
        self.assertEqual(metrics["read_num"], 20)

    def test_different_itemidx_or_date_are_separate_rows(self) -> None:
        store.upsert(self.con, "publish_list", "2026-10-16", "m1", 1, "a", {})
        store.upsert(self.con, "publish_list", "2026-10-16", "m1", 2, "b", {})
        store.upsert(self.con, "publish_list", "2026-10-17", "m1", 1, "c", {})
        self.con.commit()
        self.assertEqual(len(self.rows()), 3)

    def test_metrics_stored_as_json_and_types_coerced(self) -> None:
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-16",
            12345,
            "1",
            "t",
            {"finish_read_rate": 0.25},
            fetched_at="2026-10-17T09:00:00+08:00",
        )
        self.con.commit()
        r = self.rows()[0]
        self.assertEqual(r[2], "12345")  # appmsgid 归一为 TEXT
        self.assertEqual(r[3], 1)  # itemidx 归一为 INTEGER
        self.assertEqual(r[6], "2026-10-17T09:00:00+08:00")
        self.assertEqual(json.loads(r[5]), {"finish_read_rate": 0.25})


class TestLogRun(StoreBase):
    def test_journal_rows_and_commit(self) -> None:
        store.log_run(self.con, "fetch:publish_list", True, "3 articles")
        store.log_run(self.con, "fetch:datacube", False, "boom")
        rows = self.rows("journal")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], "fetch:publish_list")
        self.assertEqual(rows[0][3], 1)  # ok=True → 1
        self.assertEqual(rows[1][3], 0)  # ok=False → 0
        # log_run 自带 commit：新连接也能读到
        con2 = sqlite3.connect(self.db)
        self.assertEqual(len(con2.execute("SELECT * FROM journal").fetchall()), 2)
        con2.close()


class TestExportCsv(StoreBase):
    def test_export_union_of_metric_keys(self) -> None:
        csv_path = os.path.join(self._tmp.name, "out.csv")
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-15",
            "mA",
            1,
            "甲",
            {"finish_read_rate": 0.1, "total_pv": 100},
        )
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-16",
            "mB",
            1,
            "乙",
            {"finish_read_rate": 0.2, "read_rate": 0.5},
        )  # 键集不同 → 并集
        store.upsert(
            self.con, "publish_list", "2026-10-16", "mB", 1, "乙", {"read_num": 9}
        )  # 其它 source 不导出
        self.con.commit()
        n = store.export_csv(self.con, "datacube_article", csv_path)
        self.assertEqual(n, 2)
        lines = Path(csv_path).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(
            lines[0], "stat_date,appmsgid,itemidx,title,finish_read_rate,read_rate,total_pv"
        )
        self.assertEqual(len(lines), 3)  # 表头 + 2 数据行；按日期排序
        self.assertEqual(lines[1].split(",")[1], "mA")
        self.assertIn(",100", lines[1])  # 缺键行补空串
        self.assertEqual(lines[2].split(",")[1], "mB")
        self.assertTrue(lines[2].endswith(","))  # total_pv 缺失 → 空列

    def test_export_empty_source_returns_zero_rows(self) -> None:
        csv_path = self._tmp.name + "/empty.csv"
        self.assertEqual(store.export_csv(self.con, "nothing", csv_path), 0)
        self.assertEqual(
            len(Path(csv_path).read_text(encoding="utf-8").strip().splitlines()), 1
        )  # 仅表头


class TestExportJson(StoreBase):
    def test_export_json_payload_and_meta(self) -> None:
        out = self._tmp.name + "/out.json"
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-15",
            "mA",
            1,
            "甲",
            {"finish_read_rate": 0.1, "total_pv": 100},
        )
        store.upsert(
            self.con, "datacube_article", "2026-10-16", "mB", 1, "乙", {"read_rate": 0.5}
        )  # 键集不同 → 并集+缺键补空
        self.con.commit()
        n = store.export_json(self.con, "datacube_article", out)
        self.assertEqual(n, 2)
        p = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(p["source"], "datacube_article")
        self.assertEqual(p["row_count"], 2)
        self.assertEqual(
            p["columns"],
            [
                "stat_date",
                "appmsgid",
                "itemidx",
                "title",
                "finish_read_rate",
                "read_rate",
                "total_pv",
            ],
        )
        self.assertEqual([r["appmsgid"] for r in p["records"]], ["mA", "mB"])
        self.assertEqual(p["records"][0]["total_pv"], 100)
        self.assertNotIn("total_pv", p["records"][1])  # 缺键=字段缺失（结构化语义）
        # 口径元数据内嵌：列结构与 CSV 伴随文件一致
        self.assertEqual(
            p["metrics_meta"]["columns"],
            ["column", "source", "window", "denominator", "unit", "cross_source_comparable"],
        )
        cols = [row[0] for row in p["metrics_meta"]["rows"]]
        self.assertEqual(cols, ["finish_read_rate", "read_rate", "total_pv"])

    def test_export_json_no_meta(self) -> None:
        out = self._tmp.name + "/nometa.json"
        store.upsert(self.con, "publish_list", "2026-10-16", "mB", 1, "乙", {"read_num": 9})
        self.con.commit()
        store.export_json(self.con, "publish_list", out, with_meta=False)
        p = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertNotIn("metrics_meta", p)
        self.assertEqual(p["records"][0]["read_num"], 9)

    def test_export_json_empty_source(self) -> None:
        out = self._tmp.name + "/empty.json"
        self.assertEqual(store.export_json(self.con, "nothing", out), 0)
        p = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(p["row_count"], 0)
        self.assertEqual(p["records"], [])
        self.assertEqual(p["metrics_meta"]["rows"], [])  # 空 source 仍带元数据表头


class TestExportCsvMeta(StoreBase):
    def test_sidecar_metrics_csv_written(self) -> None:
        csv_path = os.path.join(self._tmp.name, "out.csv")
        store.upsert(
            self.con,
            "datacube_article",
            "2026-10-15",
            "mA",
            1,
            "甲",
            {"finish_read_rate": 0.1, "total_pv": 100},
        )
        self.con.commit()
        n = store.export_csv(self.con, "datacube_article", csv_path)
        self.assertEqual(n, 1)
        meta_path = csv_path + ".metrics.csv"
        self.assertTrue(os.path.exists(meta_path))
        lines = Path(meta_path).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(lines[0], "column,source,window,denominator,unit,cross_source_comparable")
        # 每个指标列一行，口径来自注册表
        self.assertEqual(len(lines), 3)  # 表头 + finish_read_rate + total_pv
        fr = next(ln for ln in lines[1:] if ln.startswith("finish_read_rate"))
        self.assertIn("datacube_article", fr)
        self.assertIn("7 天窗口", fr)
        self.assertTrue(fr.endswith(",否"))  # 完读率不可跨源比

    def test_sidecar_marks_unregistered_keys(self) -> None:
        csv_path = os.path.join(self._tmp.name, "u.csv")
        store.upsert(
            self.con,
            "article_detail",
            "2026-10-15",
            "mD",
            1,
            "丁",
            {"read_uv": 5, "some_new_upstream_field": 1},
        )
        self.con.commit()
        store.export_csv(self.con, "article_detail", csv_path)
        lines = Path(csv_path + ".metrics.csv").read_text(encoding="utf-8").strip().splitlines()
        new = next(ln for ln in lines if ln.startswith("some_new_upstream_field"))
        self.assertIn("(未登记)", new)
        known = next(ln for ln in lines if ln.startswith("read_uv"))
        self.assertIn("30 天窗口", known)  # 登记键来自注册表口径

    def test_no_meta_disables_sidecar(self) -> None:
        csv_path = os.path.join(self._tmp.name, "nometa.csv")
        store.upsert(self.con, "publish_list", "2026-10-15", "mE", 1, "戊", {"read_num": 3})
        self.con.commit()
        n = store.export_csv(self.con, "publish_list", csv_path, with_meta=False)
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(csv_path + ".metrics.csv"))

    def test_sidecar_empty_source_only_header(self) -> None:
        csv_path = os.path.join(self._tmp.name, "empty2.csv")
        store.export_csv(self.con, "nothing", csv_path)
        self.assertEqual(
            Path(csv_path + ".metrics.csv").read_text(encoding="utf-8").strip().splitlines(),
            ["column,source,window,denominator,unit,cross_source_comparable"],
        )

    def test_csv_metric_meta_from_registry(self) -> None:
        rows = store.csv_metric_meta("publish_list", ["read_num"])
        self.assertEqual(rows[0][0], "read_num")
        self.assertEqual(rows[0][1], "publish_list")
        self.assertEqual(rows[0][2], "实时累计（拉取时点全量）")


if __name__ == "__main__":
    unittest.main()
