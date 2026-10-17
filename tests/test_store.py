"""store 单元测试（T2）：connect 建库 / upsert 幂等 / log_run / export_csv。
运行：python3 tests/test_store.py  （unittest，零第三方依赖）
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import store                                  # noqa: E402


class StoreBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, 'data', 'mpdt.sqlite3')
        self.con = store.connect(self.db)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()

    def rows(self, table='snapshots'):
        return self.con.execute(f'SELECT * FROM {table}').fetchall()


class TestConnect(StoreBase):
    def test_creates_db_and_schema(self):
        self.assertTrue(os.path.exists(self.db))
        # 建库时自动创建 snapshots / journal 两表
        tables = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('snapshots', tables)
        self.assertIn('journal', tables)

    def test_reconnect_keeps_data(self):
        store.upsert(self.con, 'publish_list', '2026-10-16', 'm1', 1, 't', {'read_num': 1})
        self.con.commit()
        con2 = store.connect(self.db)               # 二次 connect 幂等（CREATE IF NOT EXISTS）
        self.assertEqual(len(con2.execute('SELECT * FROM snapshots').fetchall()), 1)
        con2.close()


class TestUpsertIdempotent(StoreBase):
    def test_same_pk_twice_keeps_single_row(self):
        store.upsert(self.con, 'publish_list', '2026-10-16', 'm1', 1, '旧标题',
                     {'read_num': 10, 'share_num': 1})
        store.upsert(self.con, 'publish_list', '2026-10-16', 'm1', 1, '新标题',
                     {'read_num': 20, 'share_num': 2})
        self.con.commit()
        rows = self.rows()
        self.assertEqual(len(rows), 1, '同主键重复 upsert 应只保留一行')
        title, metrics = rows[0][4], json.loads(rows[0][5])
        self.assertEqual(title, '新标题')            # 后写覆盖（取最新快照）
        self.assertEqual(metrics['read_num'], 20)

    def test_different_itemidx_or_date_are_separate_rows(self):
        store.upsert(self.con, 'publish_list', '2026-10-16', 'm1', 1, 'a', {})
        store.upsert(self.con, 'publish_list', '2026-10-16', 'm1', 2, 'b', {})
        store.upsert(self.con, 'publish_list', '2026-10-17', 'm1', 1, 'c', {})
        self.con.commit()
        self.assertEqual(len(self.rows()), 3)

    def test_metrics_stored_as_json_and_types_coerced(self):
        store.upsert(self.con, 'datacube_article', '2026-10-16', 12345, '1', 't',
                     {'finish_read_rate': 0.25}, fetched_at='2026-10-17T09:00:00+08:00')
        self.con.commit()
        r = self.rows()[0]
        self.assertEqual(r[2], '12345')              # appmsgid 归一为 TEXT
        self.assertEqual(r[3], 1)                    # itemidx 归一为 INTEGER
        self.assertEqual(r[6], '2026-10-17T09:00:00+08:00')
        self.assertEqual(json.loads(r[5]), {'finish_read_rate': 0.25})


class TestLogRun(StoreBase):
    def test_journal_rows_and_commit(self):
        store.log_run(self.con, 'fetch:publish_list', True, '3 articles')
        store.log_run(self.con, 'fetch:datacube', False, 'boom')
        rows = self.rows('journal')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], 'fetch:publish_list')
        self.assertEqual(rows[0][3], 1)              # ok=True → 1
        self.assertEqual(rows[1][3], 0)              # ok=False → 0
        # log_run 自带 commit：新连接也能读到
        con2 = sqlite3.connect(self.db)
        self.assertEqual(len(con2.execute('SELECT * FROM journal').fetchall()), 2)
        con2.close()


class TestExportCsv(StoreBase):
    def test_export_union_of_metric_keys(self):
        csv_path = os.path.join(self._tmp.name, 'out.csv')
        store.upsert(self.con, 'datacube_article', '2026-10-15', 'mA', 1, '甲',
                     {'finish_read_rate': 0.1, 'total_pv': 100})
        store.upsert(self.con, 'datacube_article', '2026-10-16', 'mB', 1, '乙',
                     {'finish_read_rate': 0.2, 'read_rate': 0.5})   # 键集不同 → 并集
        store.upsert(self.con, 'publish_list', '2026-10-16', 'mB', 1, '乙',
                     {'read_num': 9})                                # 其它 source 不导出
        self.con.commit()
        n = store.export_csv(self.con, 'datacube_article', csv_path)
        self.assertEqual(n, 2)
        lines = open(csv_path).read().strip().splitlines()
        self.assertEqual(lines[0], 'stat_date,appmsgid,itemidx,title,finish_read_rate,read_rate,total_pv')
        self.assertEqual(len(lines), 3)              # 表头 + 2 数据行；按日期排序
        self.assertEqual(lines[1].split(',')[1], 'mA')
        self.assertIn(',100', lines[1])              # 缺键行补空串
        self.assertEqual(lines[2].split(',')[1], 'mB')
        self.assertTrue(lines[2].endswith(','))      # total_pv 缺失 → 空列

    def test_export_empty_source_returns_zero_rows(self):
        csv_path = os.path.join(self._tmp.name, 'empty.csv')
        self.assertEqual(store.export_csv(self.con, 'nothing', csv_path), 0)
        self.assertEqual(len(open(csv_path).read().strip().splitlines()), 1)  # 仅表头


if __name__ == '__main__':
    unittest.main()
