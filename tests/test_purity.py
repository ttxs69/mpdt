"""FP core / imperative shell 的分界线，用测试钉住。

架构一旦只写在文档里就会退化：下一个人（或下一轮的我）想在纯函数里加一行
`print` 调试，忘了删，边界就没了。这个文件把"纯"变成一个可执行的断言。

规则：
  - 列入 `PURE_MODULES` 的模块不许 import 任何 I/O 模块，也不许调用
    print / open / execute / fetchall / commit / urlopen。
  - 纯函数层的数据处理函数必须是"同输入同输出"，且不读全局可变状态。

壳层（report.collect / _write / generate、cli 的 cmd_*）当然可以 I/O —— 它们就
是干这个的，只是必须薄。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 纯函数层：只有这些模块被要求"零 I/O"。新增纯模块时加到这里。
PURE_MODULES = (
    "mpdt/render.py",  # 数据 → Markdown / HTML / 邮件 / 人读行
    "mpdt/shape.py",  # 数据 → 数据
    "mpdt/advice.py",  # 诊断：行 → 判定
    "mpdt/next_step.py",  # 判定 → 下一步文案
    "mpdt/metrics.py",  # 口径登记表与查询视图
    "mpdt/parsing.py",  # 原始文本 → 结构化数据（fetchers 的纯半边）
)

# 壳层：允许查库/发信/写文件/打印，但每个函数都必须短。
SHELL_MODULES = (
    "mpdt/cli.py",
    "mpdt/push.py",
    "mpdt/report.py",
    "mpdt/store.py",
    "mpdt/fetchers/publish_list.py",
    "mpdt/fetchers/datacube.py",
    "mpdt/fetchers/article_detail.py",
)
SHELL_LIMIT = 30

# 出现这些 import 说明模块在读环境/文件/网络/数据库
FORBIDDEN_IMPORTS = {
    "os",
    "sys",
    "io",
    "sqlite3",
    "urllib",
    "socket",
    "subprocess",
    "shutil",
    "pathlib",
    "tempfile",
    "logging",
    "smtplib",
    "imaplib",
    "websocket",
}

# 出现这些调用说明模块产生了副作用
FORBIDDEN_CALLS = {
    "print",
    "input",
    "open",
    "exec",
    "eval",
    "compile",
}

# 出现这些方法名说明在操作数据库/网络（属性名足够独特，不会误伤）
FORBIDDEN_ATTRS = {
    "execute",
    "executemany",
    "fetchall",
    "fetchone",
    "commit",
    "urlopen",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "mkdir",
    "makedirs",
}


def module_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def forbidden_findings(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                out.append(f"line {node.lineno}: calls {fn.id}()")
            elif isinstance(fn, ast.Attribute) and fn.attr in FORBIDDEN_ATTRS:
                out.append(f"line {node.lineno}: calls .{fn.attr}()")
    return out


def oversized_functions(source: str, limit: int) -> list[tuple[str, int]]:
    """[(函数名, 语句数)] —— 超过 limit 的壳函数。语句数含嵌套。"""
    out: list[tuple[str, int]] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n = sum(1 for child in ast.walk(node) if isinstance(child, ast.stmt))
            if n > limit:
                out.append((node.name, n))
    return out


class TestPureModulesHaveNoSideEffects(unittest.TestCase):
    def test_pure_modules_exist(self) -> None:
        for rel in PURE_MODULES:
            self.assertTrue((ROOT / rel).is_file(), f"{rel} 不见了 —— 更新 PURE_MODULES")

    def test_pure_modules_import_no_io(self) -> None:
        for rel in PURE_MODULES:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            imported = module_imports(tree) & FORBIDDEN_IMPORTS
            self.assertFalse(
                imported, f"{rel} 是纯函数层，却 import 了 I/O 模块：{sorted(imported)}"
            )

    def test_pure_modules_make_no_io_calls(self) -> None:
        for rel in PURE_MODULES:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            findings = forbidden_findings(tree)
            self.assertFalse(findings, f"{rel} 是纯函数层，却出现了副作用调用：{findings[:5]}")

    def test_pure_layers_never_import_a_shell(self) -> None:
        """纯层不许反向依赖壳层 —— 否则"纯"只是名字，一 import 就带进 I/O。"""
        shells = {
            f.replace("/", ".").removesuffix(".py").split(".", 1)[1]
            for f in (*SHELL_MODULES, "mpdt/session.py")
        }
        for rel in PURE_MODULES:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                dep = node.module.split(".")[-1]
                self.assertNotIn(dep, shells, f"{rel} 反向依赖了壳层模块 {node.module}")

    def test_the_guard_actually_bites(self) -> None:
        """守卫本身要被验证：一段带 I/O 的源码必须被判不纯。

        不然"0 findings"可能只是扫描写错了（比如永远返回空列表）。
        """
        bad = """
import os
def f():
    print("x")
    return open("y").read()
"""
        tree = ast.parse(bad)
        self.assertIn("os", module_imports(tree) & FORBIDDEN_IMPORTS)
        findings = forbidden_findings(tree)
        self.assertTrue(any("print" in f for f in findings), findings)
        self.assertTrue(any("open" in f for f in findings), findings)


class TestTheShellStaysThin(unittest.TestCase):
    """壳层可以有 I/O，但每个壳函数都该短。超过这个长度的壳通常是逻辑漏回来了。

    不维护函数白名单：**每个**函数自动受限。否则新增一个 200 行的 cmd_xxx
    就永远不会被这条检查看见 —— 白名单只保护它认识的东西。
    """

    def test_every_shell_function_stays_short(self) -> None:
        offenders: dict[str, list[str]] = {}
        for rel in SHELL_MODULES:
            big = oversized_functions((ROOT / rel).read_text(encoding="utf-8"), SHELL_LIMIT)
            if big:
                offenders[rel] = [f"{name}={n} 语句" for name, n in big]
        self.assertEqual(
            offenders,
            {},
            f"壳函数超过 {SHELL_LIMIT} 条语句：拆开（决策/文案搬到纯层），别调大数字",
        )

    def test_the_length_guard_bites(self) -> None:
        """长度检查也要被验证：35 条语句的函数必须被判超标。"""
        stmts = "\n".join(f"    x{i} = {i}" for i in range(35))
        flagged = oversized_functions(f"def fat():\n{stmts}\n", SHELL_LIMIT)
        self.assertTrue(flagged, "35 条语句的函数没被判超标")
        self.assertEqual(oversized_functions("def thin():\n    return 1\n", SHELL_LIMIT), [])


class TestTheDataTransformsArePure(unittest.TestCase):
    """抽查纯函数的行为：同输入同输出，且不依赖外部状态。"""

    def test_report_rendering_is_deterministic(self) -> None:
        from mpdt import render

        data = {
            "detail_rows": [
                (
                    "2026-10-12",
                    "文章A",
                    '{"publish_date":"2026-10-12","read_uv":100,'
                    '"finished_read_pv_ratio":0.25,"channel_breakdown":[]}',
                )
            ],
            "dc_rows": [],
            "series_by_source": {},
            "week_keys": set(),
            "detail_in_week": [],
            "advice_rows": [],
        }
        first = render.build_report("2026-W42", "2026-10-12", "2026-10-18", data)
        second = render.build_report("2026-W42", "2026-10-12", "2026-10-18", data)
        self.assertEqual(first, second)
        md, payload = first
        self.assertIn("公众号数据周报 2026-W42", md)
        self.assertIn("口径说明", md)
        self.assertIsInstance(payload["rows"], int)

    def test_build_report_does_not_mutate_its_input(self) -> None:
        from mpdt import render

        rows = [("2026-10-12", "文章A", '{"publish_date":"2026-10-12","read_uv":10}')]
        data = {
            "detail_rows": rows,
            "dc_rows": [],
            "series_by_source": {"article_detail": {}},
            "week_keys": set(),
            "detail_in_week": [],
            "advice_rows": [],
        }
        snapshot = list(rows)
        render.build_report("2026-W42", "2026-10-12", "2026-10-18", data)
        self.assertEqual(data["detail_rows"], snapshot)


class TestShapeIsPure(unittest.TestCase):
    """整形规则就是口径本身，所以单独验：同输入同输出，不依赖外部状态。"""

    def test_select_recent_articles_drops_old_and_dedups(self) -> None:
        from mpdt import shape

        articles = [
            {"appmsgid": 1, "itemidx": 1, "publish_date": "2026-10-14"},
            {"appmsgid": 1, "itemidx": 1, "publish_date": "2026-10-14"},  # 重复
            {"appmsgid": 2, "itemidx": 1, "publish_date": "2026-10-01"},  # 早于 cutoff
            {"appmsgid": 3, "itemidx": 1, "publish_date": ""},  # 缺发表日
            {"appmsgid": 4, "itemidx": 2, "publish_date": "2026-10-12"},
        ]
        got = shape.select_recent_articles(articles, "2026-10-05")
        self.assertEqual(got, [("1", 1, "2026-10-14"), ("4", 2, "2026-10-12")])

    def test_detail_metrics_derives_total_read_user_from_all_scene(self) -> None:
        from mpdt import shape

        d = {
            "metrics": {"read_uv": 100},
            "publish_date": "2026-10-12",
            "window_days": 30,
            "channels": [
                {"scene": 0, "read_user": 60},
                {"scene": 6, "read_user": 30},
                {"scene": 7, "read_user": 10},
            ],
            "channel_breakdown": [{"scene_label": "推荐", "read_pct": 30.0}],
        }
        m = shape.detail_metrics(d, all_scene=0)
        self.assertEqual(m["total_read_user"], 60)  # 只累加「全部」渠道
        self.assertEqual(m["window_days"], 30)
        self.assertEqual(m["read_uv"], 100)

    def test_detail_view_summary_and_line_come_from_the_same_numbers(self) -> None:
        from mpdt import shape

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
        metrics = {"read_uv": 100, "finished_read_pv_ratio": 0.25}
        summary, line = shape.detail_view(d, metrics)
        self.assertEqual(summary["recommend_pct"], 45.0)
        self.assertIn("25.0%", line)
        self.assertIn("45.0%", line)

    def test_export_columns_are_sorted_and_stable(self) -> None:
        from mpdt import shape

        rows = [
            ("2026-10-12", "1", 1, "A", '{"b":1,"a":2}'),
            ("2026-10-13", "1", 1, "A", '{"c":3,"a":4}'),
        ]
        self.assertEqual(shape.export_columns(rows), ["a", "b", "c"])
        self.assertEqual(shape.export_columns(list(reversed(rows))), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
