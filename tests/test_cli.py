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
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mpdt.fetchers  # noqa: E402
import mpdt.fetchers.article_detail  # noqa: E402
import mpdt.fetchers.datacube  # noqa: E402
import mpdt.fetchers.publish_list  # noqa: E402  预导入以可打补丁
from mpdt import cli, config  # noqa: E402
from mpdt import store as real_store  # noqa: E402
from mpdt.errors import AuthError, UpstreamError  # noqa: E402


class _StubSession:
    """替身 Session：仅承载 cfg，验证 Session(cfg) 被构造。"""

    def __init__(self, cfg: Any) -> Any:
        self.cfg = cfg


def _pl_stub(articles: Any) -> Any:
    mod = mock.MagicMock()
    mod.fetch = lambda s, count=20: {"articles": articles}
    return mod


def _dc_stub(rows: Any) -> Any:
    mod = mock.MagicMock()
    mod.fetch = lambda s, b, e: rows
    return mod


def _ad_stub(page: Any) -> Any:
    mod = mock.MagicMock()
    mod.fetch = lambda s, mid, idx, pub: page
    mod.ALL_SCENE = -1
    return mod


class CliBase(unittest.TestCase):
    def setUp(self) -> Any:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_cfg = (config.CONFIG_DIR, config.CONFIG_PATH, config.DEFAULTS["db_path"])
        config.CONFIG_DIR = self._tmp.name
        config.CONFIG_PATH = os.path.join(self._tmp.name, "config.json")
        config.DEFAULTS["db_path"] = os.path.join(self._tmp.name, "mpdt.sqlite3")
        config.save(dict(config.DEFAULTS))
        self._saved_flag, self._saved_cmd, self._saved_res = (
            cli.JSON_FLAG[0],
            cli.CMD_NAME[0],
            cli.RES[0],
        )
        self._stdout, self._stderr = sys.stdout, sys.stderr

    def tearDown(self) -> Any:
        config.CONFIG_DIR, config.CONFIG_PATH, dbp = self._saved_cfg
        config.DEFAULTS["db_path"] = dbp
        cli.JSON_FLAG[0], cli.CMD_NAME[0], cli.RES[0] = (
            self._saved_flag,
            self._saved_cmd,
            self._saved_res,
        )
        sys.stdout, sys.stderr = self._stdout, self._stderr
        self._tmp.cleanup()

    def run_cli(self, argv, patch_fetch=None, patch_detail=None) -> tuple[int, str, str]:
        """跑 cli.main(argv)，返回 (rc, stdout, stderr)。today 固定为 2026-10-17。"""
        import contextlib

        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        extras = list(self._extra_patches(patch_fetch, patch_detail))
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch("mpdt.session.Session", _StubSession))
                stack.enter_context(mock.patch("sys.stdout.isatty", return_value=True))
                stack.enter_context(
                    mock.patch.object(cli, "today_shanghai", return_value=dt.date(2026, 10, 17))
                )
                for name, repl in extras:
                    stack.enter_context(mock.patch.object(mpdt.fetchers, name, repl))
                rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        return rc, buf_out.getvalue(), buf_err.getvalue()

    @staticmethod
    def _extra_patches(patch_fetch, patch_detail) -> list[Any]:
        out = []
        if patch_fetch:
            out.append(("publish_list", patch_fetch[0]))
            out.append(("datacube", patch_fetch[1]))
        if patch_detail:
            out.append(("article_detail", patch_detail))
        return out

    # ---- 供各用例构造快照数据 ----
    def seed_db(self) -> None:
        con = real_store.connect(config.DEFAULTS["db_path"])
        real_store.upsert(
            con,
            "datacube_article",
            "2026-10-15",
            "mA",
            1,
            "甲",
            {
                "refdate": "2026-10-15",
                "finish_read_rate": 0.3,
                "read_rate": 0.5,
                "total_pv": 10,
                "total_share": 1,
            },
        )
        con.commit()
        con.close()

    @staticmethod
    def pl_article(**kw) -> Any:
        a = {
            "appmsgid": "mA",
            "itemidx": 1,
            "title": "甲",
            "read_num": 8,
            "share_num": 1,
            "like_num": 2,
            "old_like_num": 0,
            "comment_num": 0,
            "publish_date": "2026-10-16",
        }
        a.update(kw)
        return a

    @staticmethod
    def detail_page() -> Any:
        return {
            "appmsgid": "mD",
            "itemidx": 1,
            "title": "丁",
            "publish_date": "2026-10-16",
            "window_days": 30,
            "metrics": {"read_uv": 40, "finished_read_pv_ratio": 0.25, "avg_article_read_time": 11},
            "channels": [{"scene": -1, "scene_label": "全部", "read_user": 40, "read_pct": 100.0}],
            "channel_breakdown": [
                {"scene": 6, "scene_label": "推荐", "read_user": 20, "read_pct": 50.0},
                {"scene": 0, "scene_label": "公众号消息", "read_user": 8, "read_pct": 20.0},
            ],
        }


class TestInit(CliBase):
    def test_init_creates_config_exit0(self) -> None:
        os.remove(config.CONFIG_PATH)
        rc, out, _err = self.run_cli(["init"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(config.CONFIG_PATH))
        self.assertIn("完成", out)

    def test_init_json_payload_shape(self) -> None:
        os.remove(config.CONFIG_PATH)
        rc, out, _err = self.run_cli(["--json", "init"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(set(p), {"ok", "cmd", "data", "meta"})
        self.assertEqual(p["ok"], True)
        self.assertEqual(p["cmd"], "init")
        self.assertEqual(p["data"]["config_path"], config.CONFIG_PATH)
        self.assertEqual(p["data"]["existed"], False)

    def test_init_when_config_exists_reports_existed(self) -> None:
        rc, out, _err = self.run_cli(["--json", "init"])  # setUp 已 save → 已存在
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["data"]["existed"], True)  # 已存在信息走 data（--json 下人读文本不输出）


class TestReport(CliBase):
    def test_report_default_out_dir(self) -> None:
        self.seed_db()
        cwd = os.getcwd()
        os.chdir(self._tmp.name)
        try:
            rc, _out, _err = self.run_cli(["report", "--week", "2026-W42"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists("mpdt-reports/weekly-2026-W42.md"))
        finally:
            os.chdir(cwd)

    def test_report_json_meta_rows_week(self) -> None:
        self.seed_db()
        out_dir = os.path.join(self._tmp.name, "r")
        rc, out, _err = self.run_cli(["--json", "report", "--week", "2026-W42", "--out", out_dir])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["ok"], True)
        self.assertEqual(p["cmd"], "report")
        self.assertEqual(p["data"]["rows"], 1)
        self.assertEqual(p["data"]["week"], "2026-W42")
        self.assertEqual(p["meta"], {})  # meta 与 data 分离：week 属 data
        self.assertTrue(p["data"]["md"].endswith("weekly-2026-W42.md"))
        self.assertTrue(os.path.exists(p["data"]["html"]))
        # 新增：周报应声明 concepts_compared / concepts_empty（区分有数据 vs 折叠）
        self.assertIn("concepts_compared", p["data"])
        self.assertIn("concepts_empty", p["data"])
        self.assertIsInstance(p["data"]["concepts_compared"], list)
        self.assertIsInstance(p["data"]["concepts_empty"], list)

    def test_report_week_defaults_to_current(self) -> None:
        # 不给 --week → 取上海当天所在 ISO 周（today 已 stub 到 2026-10-17 = W42）
        self.seed_db()
        out_dir = os.path.join(self._tmp.name, "r2")
        rc, out, _err = self.run_cli(["--json", "report", "--out", out_dir])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["week"], "2026-W42")


class TestExport(CliBase):
    def test_export_writes_csv_exit0(self) -> None:
        self.seed_db()
        csv_path = os.path.join(self._tmp.name, "x.csv")
        rc, _out, _err = self.run_cli(["export", "--source", "datacube_article", "--out", csv_path])
        self.assertEqual(rc, 0)
        self.assertEqual(len(open(csv_path).read().strip().splitlines()), 2)

    def test_export_json_payload(self) -> None:
        self.seed_db()
        csv_path = os.path.join(self._tmp.name, "x2.csv")
        _rc, out, _err = self.run_cli(["--json", "export", "--out", csv_path])
        p = json.loads(out)
        self.assertEqual(p["cmd"], "export")
        self.assertEqual(
            p["data"],
            {
                "out": csv_path,
                "rows": 1,
                "source": "datacube_article",
                "format": "csv",
                "metrics_meta": csv_path + ".metrics.csv",
            },
        )
        self.assertTrue(os.path.exists(csv_path + ".metrics.csv"))

    def test_export_no_meta_flag_skips_sidecar(self) -> None:
        self.seed_db()
        csv_path = os.path.join(self._tmp.name, "x3.csv")
        _rc, out, _err = self.run_cli(["--json", "export", "--out", csv_path, "--no-meta"])
        p = json.loads(out)
        self.assertEqual(
            p["data"], {"out": csv_path, "rows": 1, "source": "datacube_article", "format": "csv"}
        )
        self.assertFalse(os.path.exists(csv_path + ".metrics.csv"))

    def test_export_empty_source_still_exit0(self) -> None:
        # 合法但无数据的源 → 0 行导出仍 exit 0（空数据=成功语义不变）
        csv_path = os.path.join(self._tmp.name, "e.csv")
        rc, out, _err = self.run_cli(
            ["--json", "export", "--source", "article_detail", "--out", csv_path]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["rows"], 0)

    def test_export_invalid_source_rejected_with_choices_hint(self) -> None:
        # UX：--source 非法值 → argparse choices 提示合法值（SystemExit 2）
        with self.assertRaises(SystemExit) as cm:
            self.run_cli(["--json", "export", "--source", "nothing"])
        self.assertEqual(cm.exception.code, 2)

    def test_export_format_json_writes_payload(self) -> None:
        self.seed_db()
        out = os.path.join(self._tmp.name, "x.json")
        rc, out_s, _err = self.run_cli(["--json", "export", "--out", out, "--format", "json"])
        self.assertEqual(rc, 0)
        p = json.loads(out_s)
        self.assertEqual(
            p["data"],
            {
                "out": out,
                "rows": 1,
                "source": "datacube_article",
                "format": "json",
                "metrics_meta": "embedded",
            },
        )
        payload = json.loads(open(out, encoding="utf-8").read())
        self.assertEqual(payload["row_count"], 1)
        self.assertIn("metrics_meta", payload)
        self.assertEqual(payload["columns"][0], "stat_date")

    def test_export_format_json_no_meta(self) -> None:
        self.seed_db()
        out = os.path.join(self._tmp.name, "nm.json")
        rc, out_s, _err = self.run_cli(
            ["--json", "export", "--out", out, "--format", "json", "--no-meta"]
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("metrics_meta", json.loads(out_s)["data"])
        payload = json.loads(open(out, encoding="utf-8").read())
        self.assertNotIn("metrics_meta", payload)

    def test_export_format_csv_default_unchanged(self) -> None:
        csv_path = os.path.join(self._tmp.name, "d.csv")
        _rc, out, _err = self.run_cli(["--json", "export", "--out", csv_path])
        self.assertEqual(json.loads(out)["data"]["format"], "csv")

    def test_export_invalid_format_rejected(self) -> None:
        with self.assertRaises(SystemExit) as cm:  # argparse choices 拒绝 → exit 2
            self.run_cli(["--json", "export", "--format", "xml"])
        self.assertEqual(cm.exception.code, 2)


class TestFetchStubbed(CliBase):
    def test_fetch_stub_exit0_and_json(self) -> None:
        pl = _pl_stub([self.pl_article()])
        dc = _dc_stub(
            [
                {
                    "refdate": "2026-10-16",
                    "appmsgid": "mA",
                    "itemidx": 1,
                    "title": "甲",
                    "finish_read_rate": 0.2,
                    "read_rate": 0.4,
                    "total_pv": 9,
                    "total_read_finish": 5,
                    "total_share": 1,
                    "send_succ": 8,
                }
            ]
        )
        rc, out, _err = self.run_cli(["--json", "fetch", "--backfill", "1"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["cmd"], "fetch")
        self.assertEqual(p["data"]["publish_list"], 1)
        self.assertEqual(p["data"]["datacube_rows"], 1)
        self.assertEqual(p["data"]["end"], "2026-10-16")  # T+1：昨天
        # 快照确实入库（stat_date=拉取日 2026-10-17）
        con = real_store.connect(config.DEFAULTS["db_path"])
        n = con.execute("SELECT COUNT(*) FROM snapshots WHERE stat_date='2026-10-17'").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)

    def test_fetch_persists_create_time_and_send_hour(self) -> None:
        """R101：publish_list 入库须带 create_time/publish_date/send_hour（元数据）。

        根因=C20 external_rules.publish_window 结构性不可消费——解析层提取了
        create_time 但 cli 入库只存 5 数值指标，发布时刻在 L1 就被丢弃。
        send_hour 口径=群发时刻（publish_info.create_time，贴图回退 sent_info.time）
        的北京时间 HH:MM。
        """
        art = self.pl_article(
            create_time=1791508200, publish_date="2026-10-09"
        )  # 10-09 09:10+08（parse 层从 create_time 派生 publish_date，恒一致）
        pl = _pl_stub([art])
        dc = _dc_stub([])
        rc, _out, _err = self.run_cli(["--json", "fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        con = real_store.connect(config.DEFAULTS["db_path"])
        row = con.execute(
            "SELECT metrics FROM snapshots WHERE stat_date='2026-10-17'"
            " AND source='publish_list' AND appmsgid='mA'"
        ).fetchone()
        con.close()
        self.assertIsNotNone(row, "publish_list 行未入库")
        m = json.loads(row[0])
        self.assertEqual(m.get("create_time"), 1791508200)
        self.assertEqual(m.get("publish_date"), "2026-10-09")
        self.assertEqual(m.get("send_hour"), "09:10")

    def test_fetch_persists_send_hour_none_when_no_create_time(self) -> None:
        art = self.pl_article(create_time=None)  # 老上游缺字段：不虚构，存 null
        pl = _pl_stub([art])
        dc = _dc_stub([])
        rc, _out, _err = self.run_cli(["--json", "fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        con = real_store.connect(config.DEFAULTS["db_path"])
        row = con.execute(
            "SELECT metrics FROM snapshots WHERE stat_date='2026-10-17'"
            " AND source='publish_list' AND appmsgid='mA'"
        ).fetchone()
        con.close()
        m = json.loads(row[0])
        self.assertIsNone(m.get("send_hour"))
        self.assertIsNone(m.get("create_time"))

    def test_fetch_from_to_dates(self) -> None:
        pl = _pl_stub([])
        dc = _dc_stub([])
        rc, out, _err = self.run_cli(
            ["--json", "fetch", "--from", "2026-10-01", "--to", "2026-10-07"], patch_fetch=(pl, dc)
        )
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["data"]["begin"], "2026-10-01")
        self.assertEqual(p["data"]["end"], "2026-10-07")

    def test_fetch_empty_data_is_success_not_error(self) -> None:
        pl, dc = _pl_stub([]), _dc_stub([])
        rc, out, _err = self.run_cli(["--json", "fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)  # 空数据=成功（T+1 提示）
        self.assertEqual(json.loads(out)["data"]["datacube_rows"], 0)


class TestDetailStubbed(CliBase):
    def test_detail_msgid_exit0_json(self) -> None:
        ad = _ad_stub(self.detail_page())
        rc, out, _err = self.run_cli(
            ["--json", "detail", "--msgid", "mD", "--publish-date", "2026-10-16"], patch_detail=ad
        )
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["cmd"], "detail")
        self.assertEqual(p["data"]["ok"], 1)
        self.assertEqual(p["data"]["items"][0]["appmsgid"], "mD")
        self.assertEqual(p["data"]["items"][0]["recommend_pct"], 50.0)

    def test_detail_msgid_requires_publish_date_exit2(self) -> None:
        # --msgid 缺 --publish-date → 退出码 2，--json 输出 ok=False（不走 r.payload 通道）
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            with mock.patch("mpdt.session.Session", _StubSession):
                rc = cli.main(["--json", "detail", "--msgid", "mD"])
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.assertEqual(rc, 2)
        p = json.loads(buf_out.getvalue())
        self.assertEqual(p["ok"], False)
        self.assertEqual(p["error"]["exit_code"], 2)


class TestMetricsCmd(CliBase):
    """mpdt metrics：按概念分组的人读视图 + --json；退出码 0；不依赖配置/网络。"""

    def test_metrics_list_human_grouped_by_concept(self) -> None:
        rc, out, _err = self.run_cli(["metrics"])
        self.assertEqual(rc, 0)
        # 按概念分组的人读：每个概念都有 `## 概念名` 标题
        self.assertIn("## 完读率", out)
        self.assertIn("finish_read_rate", out)  # series 字段名
        self.assertIn("不可直接比", out)  # 自动配对
        # 应有「跨 series 易混淆配对」小节
        self.assertIn("跨 series 易混淆配对", out)
        # 新增：应有「元数据字段」「旧版字段」小节
        self.assertIn("元数据字段", out)
        self.assertIn("旧版字段", out)
        self.assertIn("refdate", out)
        self.assertIn("old_like_num", out)

    def test_metrics_list_json_concepts_structure(self) -> None:
        rc, out, _err = self.run_cli(["--json", "metrics"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["ok"], True)
        self.assertEqual(p["cmd"], "metrics")
        # data.concepts 是 list；data.auto_pairs 是 list
        self.assertIn("concepts", p["data"])
        self.assertIn("auto_pairs", p["data"])
        self.assertEqual(p["meta"]["total_concepts"], len(p["data"]["concepts"]))
        self.assertEqual(
            p["meta"]["total_series"], sum(len(c["series"]) for c in p["data"]["concepts"])
        )
        # 新增：meta 应声明 total_meta / total_deprecated
        self.assertEqual(p["meta"]["total_meta"], 5)
        self.assertEqual(p["meta"]["total_deprecated"], 1)
        for c in p["data"]["concepts"]:
            self.assertEqual(set(c) >= {"concept", "display", "unit", "series"}, True)
            for s in c["series"]:
                self.assertEqual(
                    set(s) >= {"key", "source", "field", "window", "window_days", "denominator"},
                    True,
                )

    def test_metrics_query_finish_read_rate_shows_both_series(self) -> None:
        # "完读率" 命中 read_completion 概念 → 应同时展示 dc7 + ad30 两个 series
        rc, out, _err = self.run_cli(["metrics", "完读率"])
        self.assertEqual(rc, 0)
        self.assertIn("finish_read_rate", out)
        self.assertIn("finished_read_pv_ratio", out)
        self.assertIn("7 天窗口", out)
        self.assertIn("30 天窗口", out)
        self.assertIn("不可直接比", out)
        # 其它概念（如阅读量）不应被列出
        self.assertNotIn("total_pv", out)

    def test_metrics_query_no_match_still_exit0(self) -> None:
        rc, out, _err = self.run_cli(["--json", "metrics", "不存在的指标"])
        self.assertEqual(rc, 0)  # 空结果=成功（与空数据语义一致）
        p = json.loads(out)
        self.assertEqual(p["data"]["concepts"], [])
        self.assertEqual(p["meta"]["total_concepts"], 0)

    def test_metrics_query_read_volume_lists_three_series(self) -> None:
        # "阅读量" 概念下应有 pl / dc7 / ad30 三个 series
        rc, out, _err = self.run_cli(["--json", "metrics", "阅读量"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        c = next(c for c in p["data"]["concepts"] if c["concept"] == "read_volume")
        keys = {s["key"] for s in c["series"]}
        self.assertEqual(keys, {"pl", "dc7", "ad30"})

    def test_metrics_wow_is_its_own_concept(self) -> None:
        # 「在看」应是独立概念 wow
        rc, out, _err = self.run_cli(["--json", "metrics", "在看"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        concepts = {c["concept"] for c in p["data"]["concepts"]}
        self.assertIn("wow", concepts)
        wow = next(c for c in p["data"]["concepts"] if c["concept"] == "wow")
        fields = {s["field"] for s in wow["series"]}
        self.assertEqual(fields, {"zaikan_cnt"})

    def test_metrics_listen_split_into_two_concepts(self) -> None:
        # listen 拆成 listen_people / listen_plays 两个单 series 概念
        rc, out, _err = self.run_cli(["--json", "metrics", "听读"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        concepts = {c["concept"]: c for c in p["data"]["concepts"]}
        self.assertIn("listen_people", concepts)
        self.assertIn("listen_plays", concepts)
        self.assertEqual({s["field"] for s in concepts["listen_people"]["series"]}, {"listen_uv"})
        self.assertEqual({s["field"] for s in concepts["listen_plays"]["series"]}, {"listen_pv"})

    def test_metrics_meta_fields_excluded(self) -> None:
        # 元数据字段（refdate / publish_date / window_days / total_read_user /
        # channel_breakdown）不出现在 metrics 视图
        rc, out, _err = self.run_cli(["--json", "metrics"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        all_fields = {s["field"] for c in p["data"]["concepts"] for s in c["series"]}
        for meta in (
            "refdate",
            "publish_date",
            "window_days",
            "total_read_user",
            "channel_breakdown",
        ):
            self.assertNotIn(meta, all_fields, f"{meta} 是元数据字段，不应出现在 metrics 视图")

    def test_metrics_old_like_num_excluded_from_view(self) -> None:
        # 旧版字段不在 metrics 视图里
        _rc, out, _err = self.run_cli(["--json", "metrics"])
        p = json.loads(out)
        all_fields = {s["field"] for c in p["data"]["concepts"] for s in c["series"]}
        self.assertNotIn("old_like_num", all_fields)
        # 但「点赞」仍含新字段 like_num + like_cnt
        like = next(c for c in p["data"]["concepts"] if c["concept"] == "like")
        self.assertEqual({s["field"] for s in like["series"]}, {"like_num", "like_cnt"})

    def test_auto_pairs_present_in_json(self) -> None:
        # --json 输出的 auto_pairs 至少含 完读率 + 阅读量 + 评论 等多 series 概念
        _rc, out, _err = self.run_cli(["--json", "metrics"])
        p = json.loads(out)
        pair_concepts = {ap["concept"] for ap in p["data"]["auto_pairs"]}
        for c in ("read_completion", "read_volume", "comment", "like", "share"):
            self.assertIn(c, pair_concepts, f"auto_pairs 应自动推导概念 {c}")
        # listen 已拆走，不再作为多 series 概念出现在 auto_pairs
        self.assertNotIn("listen", pair_concepts)


class TestVersion(CliBase):
    """--version：读 __init__.__version__，SystemExit(0)。"""

    def test_version_prints_version_exits0(self) -> None:
        from mpdt import __version__

        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["--version"])
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.assertIn(__version__, buf_out.getvalue())

    def test_version_json_ignored_clean(self) -> None:
        # --version 优先于 --json：直接打版本号退出（不产生 JSON——版本查询无 data 语义）
        from mpdt import __version__

        buf_out = io.StringIO()
        sys.stdout = buf_out
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["--json", "--version"])
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout = self._stdout
        self.assertEqual(buf_out.getvalue().strip(), "mpdt " + __version__)


class TestHelp(CliBase):
    """help 统一排版：全部子命令有中文 description；每参数有中文 help。"""

    def _get_help(self, argv: Any) -> Any:
        buf_out = io.StringIO()
        sys.stdout = buf_out
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main(argv)
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout = self._stdout
        return buf_out.getvalue()

    def test_top_help_epilog_quickstart_and_exit_codes(self) -> None:
        h = self._get_help(["--help"])
        self.assertIn("三步快速上手", h)
        self.assertIn("mpdt init", h)
        self.assertIn("mpdt fetch", h)
        self.assertIn("mpdt report", h)
        self.assertIn("退出码", h)
        self.assertIn("130", h)

    def test_all_subcommands_have_chinese_description(self) -> None:
        for name in ("init", "fetch", "detail", "report", "export", "metrics", "doctor", "status"):
            h = self._get_help([name, "--help"])
            self.assertIn("usage:", h, name)  # C locale 下 argparse 头；排版统一
            # description 含中文且不止一行参数表（有实质说明）
            self.assertRegex(h, r"[\u4e00-\u9fff]")
            self.assertNotIn("%(", h)  # 未展开的 %(default)s 类占位符是排版事故

    def test_every_argument_has_help_text(self) -> None:
        # 每个带参数的子命令：非空 help 逐参数存在（init/fetch/detail/report/export/metrics）
        for name in ("init", "fetch", "detail", "report", "export", "metrics"):
            h = self._get_help([name, "--help"])
            self.assertIn("--", h)
            # 粗机制断言：help 文本不应只有参数名裸列（中文帮助词大量存在）
            self.assertRegex(h, r"[\u4e00-\u9fff]{2,}")

    def test_top_help_lists_all_subcommands(self) -> None:
        h = self._get_help(["--help"])
        for name in ("init", "fetch", "detail", "report", "export", "metrics", "doctor", "status"):
            self.assertIn(name, h)


class TestDoctor(CliBase):
    """doctor：一站式体检（只读），✓/✗/⚠ 清单 + --json，exit 0。"""

    def test_doctor_no_config_reports_fail_item_and_next_step(self) -> None:
        os.remove(config.CONFIG_PATH)
        rc, out, _err = self.run_cli(["doctor"])
        self.assertEqual(rc, 0)  # 诊断≠命令失败
        self.assertIn("✗", out)
        self.assertIn("mpdt init", out)  # ✗ 项带下一步指引
        self.assertIn("体检完成", out)

    def test_doctor_json_structure_with_config(self) -> None:
        rc, out, _err = self.run_cli(["--json", "doctor"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["ok"], True)
        self.assertEqual(p["cmd"], "doctor")
        self.assertEqual(set(p), {"ok", "cmd", "data", "meta"})
        items = p["data"]["items"]
        names = {i["name"] for i in items}
        self.assertTrue(
            {"配置文件", "配置解析", "配置权限", "Chrome CDP", "token", "数据库文件"} <= names
        )
        for i in items:
            self.assertIn(i["status"], ("ok", "warn", "fail"))
        self.assertEqual(p["data"]["checks"], len(items))
        self.assertEqual(
            p["data"]["ok_count"] + p["data"]["warn_count"] + p["data"]["fail_count"], len(items)
        )

    def test_doctor_check_config_perm_600(self) -> None:
        # setUp 的 config.save 已 chmod 600 → ok 项
        _rc, out, _err = self.run_cli(["doctor"])
        self.assertIn("配置权限", out)
        self.assertIn("600", out)
        self.assertNotIn("✗ 配置权限", out)
        # 人读版有图标行
        self.assertRegex(out, r"[✓⚠✗]")

    def test_doctor_flags_world_readable_config(self) -> None:
        os.chmod(config.CONFIG_PATH, 0o644)
        rc, out, _err = self.run_cli(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("⚠ 配置权限", out)
        self.assertIn("chmod 600", out)  # 下一步指引

    def test_doctor_cdp_check_environment_agnostic(self) -> None:
        # CDP 检查结果随环境（本机是否跑了 Chrome 调试实例）而不同；
        # 机制断言：该项一定出现，且带 可达/不可达 结论之一。
        rc, out, _err = self.run_cli(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("Chrome CDP", out)
        self.assertTrue("可达" in out or "不可达" in out)
        # 人读输出三种状态图标格式统一
        for line in out.splitlines():
            if line.startswith("  ") and ("：" in line):
                self.assertRegex(line, r"[✓⚠✗]")

    def test_doctor_reports_db_rows_and_latest_snapshot(self) -> None:
        self.seed_db()
        _rc, out, _err = self.run_cli(["--json", "doctor"])
        p = json.loads(out)
        db_item = next(i for i in p["data"]["items"] if i["name"] == "数据库文件")
        self.assertEqual(db_item["rows_by_source"]["datacube_article"], 1)
        snap = next(i for i in p["data"]["items"] if i["name"] == "快照数据")
        self.assertEqual(snap["latest_snapshot"], "2026-10-15")

    def test_doctor_readonly_does_not_create_db(self) -> None:
        # 未 fetch 过：doctor 不应顺手建库（store.connect 会建表；doctor 用只读 URI）
        os.remove(config.CONFIG_PATH)
        self.run_cli(["init"])
        db = config.DEFAULTS["db_path"]
        if os.path.exists(db):
            os.remove(db)
        self.run_cli(["doctor"])
        self.assertFalse(os.path.exists(db))  # 只读体检零副作用


class TestStatus(CliBase):
    """status：db 位置 / 各源行数 / 最新快照日 / 库体积；只读。"""

    def test_status_reports_rows_paths_and_size(self) -> None:
        self.seed_db()
        rc, out, _err = self.run_cli(["status"])
        self.assertEqual(rc, 0)
        self.assertIn(config.DEFAULTS["db_path"], out)
        self.assertIn("datacube_article", out)
        self.assertIn("1 行", out)
        self.assertIn("2026-10-15", out)  # 最新快照日
        self.assertIn("库体积", out)

    def test_status_json_payload(self) -> None:
        self.seed_db()
        rc, out, _err = self.run_cli(["--json", "status"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["cmd"], "status")
        self.assertEqual(p["data"]["db_path"], config.DEFAULTS["db_path"])
        self.assertEqual(p["data"]["rows_by_source"]["datacube_article"], 1)
        self.assertEqual(p["data"]["latest_snapshot"], "2026-10-15")
        self.assertGreater(p["data"]["db_bytes"], 0)

    def test_status_no_db_no_config_still_exit0(self) -> None:
        os.remove(config.CONFIG_PATH)
        rc, out, _err = self.run_cli(["--json", "status"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertFalse(p["data"]["db_exists"])  # 库不存在→汇报而非报错


class TestProgress(CliBase):
    """fetch/detail TTY 进度行：[第 N/总]，非 TTY 静默、--json 不出。"""

    def test_fetch_progress_shows_N_of_total(self) -> None:
        pl = _pl_stub([self.pl_article(), self.pl_article(appmsgid="mB")])
        dc = _dc_stub([])
        rc, out, _err = self.run_cli(["fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        self.assertIn("[1/2]", out)
        self.assertIn("[2/2]", out)

    def test_fetch_progress_silent_in_json(self) -> None:
        pl = _pl_stub([self.pl_article()])
        dc = _dc_stub([])
        rc, out, _err = self.run_cli(["--json", "fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["data"]["publish_list"], 1)
        self.assertNotIn("[1/1]", out)  # JSON 单行纯净化


class TestErrorHints(CliBase):
    """报错文案带「下一步怎么办」指引（exit 2/3 尤甚）。"""

    @staticmethod
    def _pl_raising(exc: Any) -> Any:
        pl = mock.MagicMock()
        pl.fetch = mock.MagicMock(side_effect=exc)
        return pl

    def test_config_error_hint_mentions_doctor(self) -> None:
        os.remove(config.CONFIG_PATH)
        rc, _out, err = self.run_cli(["fetch"])
        self.assertEqual(rc, 2)
        self.assertIn("退出码 2", err)
        self.assertIn("下一步", err)
        self.assertIn("mpdt init", err)
        self.assertIn("mpdt doctor", err)

    def test_auth_error_hint_step_by_step(self) -> None:
        pl, dc = self._pl_raising(AuthError("登录态失效")), _dc_stub([])
        rc, _out, err = self.run_cli(["fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 3)
        self.assertIn("mp.weixin.qq.com", err)
        self.assertIn("mpdt doctor", err)
        self.assertIn("重新运行 mpdt fetch", err)


class TestExitCodes(CliBase):
    """退出码 2/3/4：MpdtError 子类 → 对应 exit_code，stderr 带 [mpdt] 前缀。"""

    @staticmethod
    def _pl_raising(exc: Any) -> Any:
        pl = mock.MagicMock()
        pl.fetch = mock.MagicMock(side_effect=exc)
        return pl

    def test_config_error_exit2(self) -> None:
        os.remove(config.CONFIG_PATH)
        for argv in (["report"], ["export"], ["fetch"]):
            rc, _out, err = self.run_cli(argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("退出码 2", err)

    def test_auth_error_exit3(self) -> None:
        pl, dc = self._pl_raising(AuthError("登录态失效")), _dc_stub([])
        rc, _out, err = self.run_cli(["fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 3)
        self.assertIn("[mpdt]", err)

    def test_upstream_error_exit4(self) -> None:
        pl, dc = self._pl_raising(UpstreamError("上游改版")), _dc_stub([])
        rc, _out, _err = self.run_cli(["fetch"], patch_fetch=(pl, dc))
        self.assertEqual(rc, 4)

    def test_error_in_json_mode_stdout_kept_clean(self) -> None:
        # --json 失败路径：成功 payload 不打 stdout（错误信息走 stderr）
        os.remove(config.CONFIG_PATH)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            rc = cli.main(["--json", "report"])
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.assertEqual(rc, 2)
        self.assertEqual(buf_out.getvalue(), "")

    def test_no_subcommand_exits_argparse(self) -> None:
        # argparse required=True：无子命令 → SystemExit(2)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            with self.assertRaises(SystemExit) as cm:
                cli.main([])
            self.assertEqual(cm.exception.code, 2)
        finally:
            sys.stdout, sys.stderr = self._stdout, self._stderr


class TestModuleEntry(unittest.TestCase):
    """`python -m mpdt` 入口（__main__.py）。

    这个文件原先覆盖率 0%：没有任何测试导入它，所以它坏了也不会有人知道。
    这里用子进程跑真入口，测的是"用户实际敲的那条命令"。
    """

    def test_python_dash_m_version(self) -> None:
        import subprocess
        import sys as _sys

        root = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [_sys.executable, "-m", "mpdt", "--version"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stderr[:400])
        self.assertIn("mpdt", r.stdout or r.stderr)

    def test_python_dash_m_no_subcommand_exits_2(self) -> None:
        import subprocess
        import sys as _sys

        root = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [_sys.executable, "-m", "mpdt"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(r.returncode, 2, r.stderr[:400])


class TestPush(CliBase):
    """`mpdt push` 的五个分支（R68）。

    原先 cmd_push 覆盖率极低：它要发真邮件，所以没人敢测。
    这里把发送函数 / 回查函数 / 凭据加载器全换成替身 ——
    测的是 **cmd_push 自己的分支**（跳过 / 失败 / 送达未验证 / 确认送达 / 未验证），
    不是 SMTP。

    其中最关键的一条是「SMTP 接受但未验证送达必须 ok=False」：
    R239 的教训是"SMTP 接受 ≠ 送达"，命令层必须把未验证当成失败报出来。
    """

    def _mk_report(self, week: str = "2026-W42", out: str | None = None) -> str:
        """先生成一份周报，返回目录。"""
        out_dir = out or os.path.join(self._tmp.name, "reports")
        self.seed_db()
        rc, _o, _e = self.run_cli(["report", "--week", week, "--out", out_dir])
        self.assertEqual(rc, 0)
        return out_dir

    def test_push_generates_report_when_missing(self) -> None:
        """没生成过周报也要能推（客户不该需要两步）。"""
        from mpdt import push as push_mod

        self.seed_db()
        out_dir = os.path.join(self._tmp.name, "auto")
        with mock.patch.object(
            push_mod,
            "send_report",
            return_value={"recipient": "a@b.c", "bytes": 123},
        ) as sent:
            rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir]
            )
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "weekly-2026-W42.md")))
        self.assertEqual(sent.call_count, 1)

    def test_push_error_is_reported_as_failure(self) -> None:
        from mpdt import push as push_mod

        out_dir = self._mk_report()
        with mock.patch.object(push_mod, "send_report", side_effect=push_mod.PushError("boom")):
            rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir]
            )
        self.assertNotEqual(rc, 0)
        self.assertIn("推送失败", out)
        self.assertIn("boom", out)

    def test_push_skipped_is_reported(self) -> None:
        from mpdt import push as push_mod

        out_dir = self._mk_report()
        with mock.patch.object(
            push_mod,
            "send_report",
            return_value={"skipped": True, "recipient": "a@b.c"},
        ):
            _rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir]
            )
        self.assertIn("跳过", out)

    def test_push_verify_confirmed_delivery(self) -> None:
        from mpdt import push as push_mod

        out_dir = self._mk_report()
        with (
            mock.patch.object(
                push_mod,
                "send_report",
                return_value={"recipient": "a@b.c", "bytes": 9},
            ),
            mock.patch.object(push_mod, "_load_env", return_value={}),
            mock.patch.object(push_mod, "verify_delivery", return_value={"delivered": True}),
        ):
            rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir, "--verify"]
            )
        self.assertEqual(rc, 0, out)
        self.assertIn("已回查确认送达", out)

    def test_push_verify_unconfirmed_is_failure(self) -> None:
        """SMTP 接受但回查不到 ⇒ 必须报失败，不能报成功（R239）。"""
        from mpdt import push as push_mod

        out_dir = self._mk_report()
        with (
            mock.patch.object(
                push_mod,
                "send_report",
                return_value={"recipient": "a@b.c", "bytes": 9},
            ),
            mock.patch.object(push_mod, "_load_env", return_value={}),
            mock.patch.object(
                push_mod,
                "verify_delivery",
                return_value={"delivered": False, "error": "not found in INBOX"},
            ),
        ):
            rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir, "--verify"]
            )
        self.assertNotEqual(rc, 0)
        self.assertIn("未验证送达", out)
        self.assertIn("not found in INBOX", out)

    def test_push_without_verify_says_unverified(self) -> None:
        from mpdt import push as push_mod

        out_dir = self._mk_report()
        with mock.patch.object(
            push_mod,
            "send_report",
            return_value={"recipient": "a@b.c", "bytes": 9},
        ):
            rc, out, _err = self.run_cli(
                ["push", "--week", "2026-W42", "--to", "a@b.c", "--out", out_dir]
            )
        self.assertEqual(rc, 0, out)
        self.assertIn("SMTP 已接受，未验证收件方收到", out)


if __name__ == "__main__":
    unittest.main()
