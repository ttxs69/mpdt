"""CLI 单元测试（T2）：参数分发、退出码 0/2/3/4、--json payload {ok,cmd,data,meta}。
不碰网络/浏览器：fetch/detail 以 stub Session+fetchers 替身注入 mpdt.fetchers 命名空间。
运行：python3 tests/test_cli.py 或 unittest discover  （零第三方依赖）
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mpdt import cli, config                              # noqa: E402
from mpdt import store as real_store                      # noqa: E402
from mpdt.errors import AuthError, UpstreamError          # noqa: E402
import mpdt.fetchers                                      # noqa: E402
import mpdt.fetchers.publish_list                         # noqa: E402  预导入以可打补丁
import mpdt.fetchers.datacube                             # noqa: E402
import mpdt.fetchers.article_detail                       # noqa: E402


class _StubSession:
    """替身 Session：仅承载 cfg，验证 Session(cfg) 被构造。"""
    def __init__(self, cfg):
        self.cfg = cfg


def _pl_stub(articles):
    mod = mock.MagicMock()
    mod.fetch = lambda s, count=20: {'articles': articles}
    return mod


def _dc_stub(rows):
    mod = mock.MagicMock()
    mod.fetch = lambda s, b, e: rows
    return mod


def _ad_stub(page):
    mod = mock.MagicMock()
    mod.fetch = lambda s, mid, idx, pub: page
    mod.ALL_SCENE = -1
    return mod


class CliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_cfg = (config.CONFIG_DIR, config.CONFIG_PATH, config.DEFAULTS['db_path'])
        config.CONFIG_DIR = self._tmp.name
        config.CONFIG_PATH = os.path.join(self._tmp.name, 'config.json')
        config.DEFAULTS['db_path'] = os.path.join(self._tmp.name, 'mpdt.sqlite3')
        config.save(dict(config.DEFAULTS))
        self._saved_flag, self._saved_cmd, self._saved_res = cli.JSON_FLAG[0], cli.CMD_NAME[0], cli.RES[0]
        self._stdout, self._stderr = sys.stdout, sys.stderr

    def tearDown(self):
        config.CONFIG_DIR, config.CONFIG_PATH, dbp = self._saved_cfg
        config.DEFAULTS['db_path'] = dbp
        cli.JSON_FLAG[0], cli.CMD_NAME[0], cli.RES[0] = self._saved_flag, self._saved_cmd, self._saved_res
        sys.stdout, sys.stderr = self._stdout, self._stderr
        self._tmp.cleanup()

    def run_cli(self, argv, patch_fetch=None, patch_detail=None):
        """跑 cli.main(argv)，返回 (rc, stdout, stderr)。today 固定为 2026-10-17。"""
        import contextlib
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        extras = list(self._extra_patches(patch_fetch, patch_detail))
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch('mpdt.session.Session', _StubSession))
                stack.enter_context(mock.patch('sys.stdout.isatty', return_value=True))
                stack.enter_context(mock.patch.object(
                    cli, 'today_shanghai', return_value=dt.date(2026, 10, 17)))
                for name, repl in extras:
                    stack.enter_context(mock.patch.object(mpdt.fetchers, name, repl))
                rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        return rc, buf_out.getvalue(), buf_err.getvalue()

    @staticmethod
    def _extra_patches(patch_fetch, patch_detail):
        out = []
        if patch_fetch:
            out.append(('publish_list', patch_fetch[0]))
            out.append(('datacube', patch_fetch[1]))
        if patch_detail:
            out.append(('article_detail', patch_detail))
        return out

    # ---- 供各用例构造快照数据 ----
    def seed_db(self):
        con = real_store.connect(config.DEFAULTS['db_path'])
        real_store.upsert(con, 'datacube_article', '2026-10-15', 'mA', 1, '甲',
                          {'refdate': '2026-10-15', 'finish_read_rate': 0.3,
                           'read_rate': 0.5, 'total_pv': 10, 'total_share': 1})
        con.commit()
        con.close()

    @staticmethod
    def pl_article(**kw):
        a = {'appmsgid': 'mA', 'itemidx': 1, 'title': '甲', 'read_num': 8,
             'share_num': 1, 'like_num': 2, 'old_like_num': 0, 'comment_num': 0,
             'publish_date': '2026-10-16'}
        a.update(kw)
        return a

    @staticmethod
    def detail_page():
        return {'appmsgid': 'mD', 'itemidx': 1, 'title': '丁', 'publish_date': '2026-10-16',
                'window_days': 30,
                'metrics': {'read_uv': 40, 'finished_read_pv_ratio': 0.25,
                            'avg_article_read_time': 11},
                'channels': [{'scene': -1, 'scene_label': '全部', 'read_user': 40, 'read_pct': 100.0}],
                'channel_breakdown': [
                    {'scene': 6, 'scene_label': '推荐', 'read_user': 20, 'read_pct': 50.0},
                    {'scene': 0, 'scene_label': '公众号消息', 'read_user': 8, 'read_pct': 20.0}]}


class TestInit(CliBase):
    def test_init_creates_config_exit0(self):
        os.remove(config.CONFIG_PATH)
        rc, out, err = self.run_cli(['init'])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(config.CONFIG_PATH))
        self.assertIn('完成', out)

    def test_init_json_payload_shape(self):
        os.remove(config.CONFIG_PATH)
        rc, out, err = self.run_cli(['--json', 'init'])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(set(p), {'ok', 'cmd', 'data', 'meta'})
        self.assertEqual(p['ok'], True)
        self.assertEqual(p['cmd'], 'init')
        self.assertEqual(p['data']['config_path'], config.CONFIG_PATH)
        self.assertEqual(p['data']['existed'], False)

    def test_init_when_config_exists_reports_existed(self):
        rc, out, err = self.run_cli(['--json', 'init'])   # setUp 已 save → 已存在
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p['data']['existed'], True)   # 已存在信息走 data（--json 下人读文本不输出）


class TestReport(CliBase):
    def test_report_default_out_dir(self):
        self.seed_db()
        cwd = os.getcwd()
        os.chdir(self._tmp.name)
        try:
            rc, out, err = self.run_cli(['report', '--week', '2026-W42'])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists('mpdt-reports/weekly-2026-W42.md'))
        finally:
            os.chdir(cwd)

    def test_report_json_meta_rows_week(self):
        self.seed_db()
        out_dir = os.path.join(self._tmp.name, 'r')
        rc, out, err = self.run_cli(['--json', 'report', '--week', '2026-W42', '--out', out_dir])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p['ok'], True)
        self.assertEqual(p['cmd'], 'report')
        self.assertEqual(p['data']['rows'], 1)
        self.assertEqual(p['data']['week'], '2026-W42')
        self.assertEqual(p['meta'], {})                # meta 与 data 分离：week 属 data
        self.assertTrue(p['data']['md'].endswith('weekly-2026-W42.md'))
        self.assertTrue(os.path.exists(p['data']['html']))

    def test_report_week_defaults_to_current(self):
        # 不给 --week → 取上海当天所在 ISO 周（today 已 stub 到 2026-10-17 = W42）
        self.seed_db()
        out_dir = os.path.join(self._tmp.name, 'r2')
        rc, out, err = self.run_cli(['--json', 'report', '--out', out_dir])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)['data']['week'], '2026-W42')


class TestExport(CliBase):
    def test_export_writes_csv_exit0(self):
        self.seed_db()
        csv_path = os.path.join(self._tmp.name, 'x.csv')
        rc, out, err = self.run_cli(['export', '--source', 'datacube_article', '--out', csv_path])
        self.assertEqual(rc, 0)
        self.assertEqual(len(open(csv_path).read().strip().splitlines()), 2)

    def test_export_json_payload(self):
        self.seed_db()
        csv_path = os.path.join(self._tmp.name, 'x2.csv')
        rc, out, err = self.run_cli(['--json', 'export', '--out', csv_path])
        p = json.loads(out)
        self.assertEqual(p['cmd'], 'export')
        self.assertEqual(p['data'], {'out': csv_path, 'rows': 1, 'source': 'datacube_article'})

    def test_export_empty_source_still_exit0(self):
        csv_path = os.path.join(self._tmp.name, 'e.csv')
        rc, out, err = self.run_cli(['--json', 'export', '--source', 'nothing', '--out', csv_path])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)['data']['rows'], 0)


class TestFetchStubbed(CliBase):
    def test_fetch_stub_exit0_and_json(self):
        pl = _pl_stub([self.pl_article()])
        dc = _dc_stub([{'refdate': '2026-10-16', 'appmsgid': 'mA', 'itemidx': 1, 'title': '甲',
                        'finish_read_rate': 0.2, 'read_rate': 0.4, 'total_pv': 9,
                        'total_read_finish': 5, 'total_share': 1, 'send_succ': 8}])
        rc, out, err = self.run_cli(['--json', 'fetch', '--backfill', '1'],
                                    patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p['cmd'], 'fetch')
        self.assertEqual(p['data']['publish_list'], 1)
        self.assertEqual(p['data']['datacube_rows'], 1)
        self.assertEqual(p['data']['end'], '2026-10-16')   # T+1：昨天
        # 快照确实入库（stat_date=拉取日 2026-10-17）
        con = real_store.connect(config.DEFAULTS['db_path'])
        n = con.execute("SELECT COUNT(*) FROM snapshots WHERE stat_date='2026-10-17'").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)

    def test_fetch_from_to_dates(self):
        pl = _pl_stub([])
        dc = _dc_stub([])
        rc, out, err = self.run_cli(['--json', 'fetch', '--from', '2026-10-01',
                                     '--to', '2026-10-07'], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p['data']['begin'], '2026-10-01')
        self.assertEqual(p['data']['end'], '2026-10-07')

    def test_fetch_empty_data_is_success_not_error(self):
        pl, dc = _pl_stub([]), _dc_stub([])
        rc, out, err = self.run_cli(['--json', 'fetch'], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)                     # 空数据=成功（T+1 提示）
        self.assertEqual(json.loads(out)['data']['datacube_rows'], 0)


class TestDetailStubbed(CliBase):
    def test_detail_msgid_exit0_json(self):
        ad = _ad_stub(self.detail_page())
        rc, out, err = self.run_cli(['--json', 'detail', '--msgid', 'mD',
                                     '--publish-date', '2026-10-16'], patch_detail=ad)
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p['cmd'], 'detail')
        self.assertEqual(p['data']['ok'], 1)
        self.assertEqual(p['data']['items'][0]['appmsgid'], 'mD')
        self.assertEqual(p['data']['items'][0]['recommend_pct'], 50.0)

    def test_detail_msgid_requires_publish_date_exit2(self):
        # --msgid 缺 --publish-date → 退出码 2，--json 输出 ok=False（不走 r.payload 通道）
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            with mock.patch('mpdt.session.Session', _StubSession):
                rc = cli.main(['--json', 'detail', '--msgid', 'mD'])
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.assertEqual(rc, 2)
        p = json.loads(buf_out.getvalue())
        self.assertEqual(p['ok'], False)
        self.assertEqual(p['error']['exit_code'], 2)


class TestExitCodes(CliBase):
    """退出码 2/3/4：MpdtError 子类 → 对应 exit_code，stderr 带 [mpdt] 前缀。"""

    @staticmethod
    def _pl_raising(exc):
        pl = mock.MagicMock()
        pl.fetch = mock.MagicMock(side_effect=exc)
        return pl

    def test_config_error_exit2(self):
        os.remove(config.CONFIG_PATH)
        for argv in (['report'], ['export'], ['fetch']):
            rc, out, err = self.run_cli(argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn('退出码 2', err)

    def test_auth_error_exit3(self):
        pl, dc = self._pl_raising(AuthError('登录态失效')), _dc_stub([])
        rc, out, err = self.run_cli(['fetch'], patch_fetch=(pl, dc))
        self.assertEqual(rc, 3)
        self.assertIn('[mpdt]', err)

    def test_upstream_error_exit4(self):
        pl, dc = self._pl_raising(UpstreamError('上游改版')), _dc_stub([])
        rc, out, err = self.run_cli(['fetch'], patch_fetch=(pl, dc))
        self.assertEqual(rc, 4)

    def test_error_in_json_mode_stdout_kept_clean(self):
        # --json 失败路径：成功 payload 不打 stdout（错误信息走 stderr）
        os.remove(config.CONFIG_PATH)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            rc = cli.main(['--json', 'report'])
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.assertEqual(rc, 2)
        self.assertEqual(buf_out.getvalue(), '')

    def test_no_subcommand_exits_argparse(self):
        # argparse required=True：无子命令 → SystemExit(2)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main([])
            self.assertEqual(cm.exception.code, 2)
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr


if __name__ == '__main__':
    unittest.main()
