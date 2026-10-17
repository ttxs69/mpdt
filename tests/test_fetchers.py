"""fixture 单元测试（T2 断言锁定真实结构，评审 WARN①）。
运行：python3 tests/test_fetchers.py  （unittest，零第三方依赖）
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, 'tests', 'fixtures')

from mpdt.fetchers.publish_list import parse_publish_list      # noqa: E402
from mpdt.fetchers.datacube import parse_tmpl17                # noqa: E402
from mpdt.fetchers.article_detail import parse_detail, channel_breakdown   # noqa: E402
from mpdt.errors import UpstreamError                          # noqa: E402


class TestPublishList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = open(os.path.join(FIX, 'publish_list.json')).read()

    def test_double_json_decode(self):
        out = parse_publish_list(self.raw)
        self.assertGreater(len(out['articles']), 0)
        a = out['articles'][0]
        for k in ('appmsgid', 'itemidx', 'title', 'read_num', 'share_num', 'like_num'):
            self.assertIn(k, a)

    def test_pic_type_gets_publish_date_from_sent_info(self):
        # R70：贴图类 publish_info 为空对象，时间戳在 sent_info.time（群发时间）。
        # 实证：2247485215 sent_info.time=1792145145 → 2026-10-16 18:05:45+08，
        # 与 PIC-rea 实查 t0（publish-read.py create_time）完全一致。
        out = parse_publish_list(self.raw)
        by_id = {a['appmsgid']: a for a in out['articles']}
        a = by_id.get(2247485215)
        self.assertIsNotNone(a, 'fixture 应含贴图样本 2247485215')
        self.assertEqual(a['publish_date'], '2026-10-16')
        self.assertEqual(a['create_time'], 1792145145)
        b = by_id.get(2247485156)
        self.assertIsNotNone(b, 'fixture 应含贴图样本 2247485156')
        self.assertEqual(b['publish_date'], '2026-10-16')

    def test_structure_change_raises_upstream_4(self):
        with self.assertRaises(UpstreamError):
            parse_publish_list('{"base_resp":{"ret":0},"publish_page":"not json"}')
        with self.assertRaises(UpstreamError):
            parse_publish_list('{"base_resp":{"ret":-1}}')

    def test_no_credentials_leak_in_fixture(self):
        self.assertNotIn('1050057230', self.raw, 'fixture 内含真实 token！')


class TestDatacube(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = open(os.path.join(FIX, 'datacube_tmpl17.json')).read()

    def test_parse_rows(self):
        rows = parse_tmpl17(self.raw)
        self.assertGreater(len(rows), 0)
        for k in ('refdate', 'finish_read_rate', 'total_pv', 'appmsgid'):
            self.assertIn(k, rows[0])

    def test_structure_change_raises_upstream_4(self):
        with self.assertRaises(UpstreamError):
            parse_tmpl17('{"unexpected": true}')
        with self.assertRaises(UpstreamError):
            parse_tmpl17(json.dumps({'base_resp': {}, 'data': [{'no_rate': 1}]}))


class TestArticleDetail(unittest.TestCase):
    """单篇渠道页（FR3②）：fixture 为 R28 真实录制（2026-10-17）。"""
    @classmethod
    def setUpClass(cls):
        cls.p_w42 = open(os.path.join(FIX, 'article_detail_2247485219_1.html')).read()
        cls.p_093 = open(os.path.join(FIX, 'article_detail_2247484945_1.html')).read()
        cls.w42 = parse_detail('200|' + cls.p_w42)
        cls.a093 = parse_detail('200|' + cls.p_093)

    def test_metrics_and_ids(self):
        self.assertEqual(self.w42['appmsgid'], '2247485219')
        self.assertEqual(self.w42['itemidx'], 1)
        self.assertEqual(self.w42['publish_date'], '2026-10-16')
        self.assertEqual(self.w42['metrics']['read_uv'], 8)
        self.assertAlmostEqual(self.w42['metrics']['finished_read_pv_ratio'], 0.25, places=6)
        self.assertEqual(self.w42['metrics']['avg_article_read_time'], 12)

    def test_scene_label_mapping(self):
        """scene→标签来自页面 JS 包 sceneTags（primary source）：6=推荐、7=搜一搜、5=其它。"""
        b = {r['scene']: r for r in self.a093['channel_breakdown']}
        self.assertEqual(b[6]['scene_label'], '推荐')
        self.assertEqual(b[6]['read_user'], 163)
        self.assertEqual(b[0]['scene_label'], '公众号消息')
        self.assertEqual(b[7]['scene_label'], '搜一搜')
        self.assertEqual(b[5]['scene_label'], '其它')
        self.assertAlmostEqual(b[6]['read_pct'], 85.8, places=1)

    def test_window_filter_7d_vs_30d(self):
        b30 = {r['scene']: r['read_user'] for r in channel_breakdown(
            self.a093['channels'], 30, self.a093['publish_date'])}
        b7 = {r['scene']: r['read_user'] for r in channel_breakdown(
            self.a093['channels'], 7, self.a093['publish_date'])}
        self.assertEqual(b30[1], 7)      # 聊天会话 发表后30天
        self.assertEqual(b7[1], 6)       # 发表后7天窗口切掉最末一天
        self.assertEqual(b7[6], 163)     # 推荐主体在首日

    def test_share_pct_can_exceed_100_sum(self):
        """口径：多渠道重复计数 → 各渠道占比之和可 >100%（页面原文）。"""
        tot = sum(r['read_pct'] for r in self.a093['channel_breakdown'])
        self.assertGreater(tot, 100.0)

    def test_structure_change_raises_upstream_4(self):
        with self.assertRaises(UpstreamError):
            parse_detail('200|<html>no data</html>')
        with self.assertRaises(UpstreamError):
            parse_detail('302|')
        with self.assertRaises(UpstreamError):
            parse_detail('200|window.wx.cgiData = {};')

    def test_no_credentials_leak_in_fixture(self):
        for raw in (self.p_w42, self.p_093):
            self.assertNotIn('1050057230', raw, 'fixture 内含真实 token！')
            self.assertNotIn('ticket', raw, 'fixture 内含会话 ticket！')
            self.assertNotIn('fingerprint', raw, 'fixture 内含 fingerprint！')

    def test_cross_source_read_uv_vs_publish_list(self):
        """AC6 跨源数值一致：单篇页 read_uv 与发表记录 read_num 同源同值（当日口径）。"""
        pl = parse_publish_list(open(os.path.join(FIX, 'publish_list.json')).read())
        same = [a for a in pl['articles'] if str(a['appmsgid']) == '2247485219']
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]['read_num'], self.w42['metrics']['read_uv'])


class TestStoreAndReport(unittest.TestCase):
    def test_idempotent_upsert(self):
        import tempfile
        from mpdt import store
        with tempfile.TemporaryDirectory() as td:
            con = store.connect(os.path.join(td, 't.sqlite3'))
            for _ in range(2):   # 同一行 upsert 两次
                store.upsert(con, 'publish_list', '2026-10-17', 1, 1, 't', {'read_num': 5})
                con.commit()
            n = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            self.assertEqual(n, 1, '幂等失败：同主键出现两行')

    def test_report_generates_with_calibration_note(self):
        import tempfile
        from mpdt import store, report
        with tempfile.TemporaryDirectory() as td:
            con = store.connect(os.path.join(td, 't.sqlite3'))
            store.upsert(con, 'datacube_article', '2026-10-15', 1, 1, '文章A',
                         {'finish_read_rate': 0.21, 'read_rate': 0.4, 'total_pv': 100,
                          'total_share': 3, 'refdate': '2026-10-15'})
            con.commit()
            out = report.generate(con, '2026-W42', td)
            md = open(out['md']).read()
            self.assertIn('口径说明', md)
            # 新口径说明按概念分组
            self.assertIn('按概念分组', md)


class TestExitSemantics(unittest.TestCase):
    def test_error_codes(self):
        from mpdt.errors import ConfigError, AuthError, UpstreamError
        self.assertEqual(ConfigError().exit_code, 2)
        self.assertEqual(AuthError().exit_code, 3)
        self.assertEqual(UpstreamError().exit_code, 4)


class TestStage6JsonTtyInterrupt(unittest.TestCase):
    """阶段 6 打磨回归：--json / TTY 静默 / Ctrl-C exit 130（T1 三态产品化）。

    环境隔离：cli.main 经 config.load() 读 ~/.mpdt/config.json——本机存在不代表
    CI runner 存在（曾被 CI 揭穿为假阳性）。本类把 CONFIG_* 与 DB 指到临时目录，
    并为 report 预置最小配置+空库，断言全部不变。
    """

    def setUp(self):
        import tempfile
        from mpdt import config as _cfg
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (_cfg.CONFIG_DIR, _cfg.CONFIG_PATH, _cfg.DEFAULTS['db_path'])
        _cfg.CONFIG_DIR = self._tmp.name
        _cfg.CONFIG_PATH = os.path.join(self._tmp.name, 'config.json')
        _cfg.DEFAULTS['db_path'] = os.path.join(self._tmp.name, 'mpdt.sqlite3')
        _cfg.save({'db_path': _cfg.DEFAULTS['db_path']})

    def tearDown(self):
        from mpdt import config as _cfg
        _cfg.CONFIG_DIR, _cfg.CONFIG_PATH, dbp = self._saved
        _cfg.DEFAULTS['db_path'] = dbp
        self._tmp.cleanup()

    def test_json_report_single_line_payload(self):
        import contextlib, io, json as j
        from mpdt import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(['--json', 'report', '--week', '2026-W42'])
        self.assertEqual(rc, 0)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, '--json 必须恰好一行：' + repr(buf.getvalue()[:200]))
        d = j.loads(lines[0])
        self.assertTrue(d['ok'])
        self.assertEqual(d['cmd'], 'report')
        self.assertIn('md', d['data'])

    def test_json_error_payload_exit_2(self):
        import contextlib, io, json as j
        from mpdt import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(['--json', 'detail', '--msgid', '123'])
        self.assertEqual(rc, 2)
        d = j.loads(buf.getvalue().strip())
        self.assertFalse(d['ok'])
        self.assertEqual(d['error']['exit_code'], 2)

    def test_keyboard_interrupt_returns_130(self):
        from mpdt import cli
        orig = cli.cmd_report
        cli.cmd_report = lambda a: (_ for _ in ()).throw(KeyboardInterrupt)
        try:
            rc = cli.main(['report'])
        finally:
            cli.cmd_report = orig
        self.assertEqual(rc, 130)

    def test_r_silent_when_quiet(self):
        from mpdt.cli import _R, JSON_FLAG
        import contextlib, io
        JSON_FLAG[0] = False
        r = _R(quiet=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r.say('过程输出应被静默')
            r.out('结果输出应出现')
        self.assertNotIn('过程输出', buf.getvalue())
        self.assertIn('结果输出', buf.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
