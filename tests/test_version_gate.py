"""Tests for tools/version_gate.py — dual-file version consistency + monotonic bump gate.

Refs: e1 R113 rule candidate — pyproject.toml and mpdt/__init__.py versions must
match AND be strictly greater than the highest existing git tag (semver).
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "tools", "version_gate.py")


def load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("version_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_repo(tmp: Any, init_ver: Any, pyproject_ver: Any, tags: Any) -> Any:
    """Create a minimal repo structure: pyproject.toml, mpdt/__init__.py, git tags."""
    os.makedirs(os.path.join(tmp, "mpdt"))
    with open(os.path.join(tmp, "pyproject.toml"), "w") as f:
        f.write(f'[project]\nname = "mpdt"\nversion = "{pyproject_ver}"\n')
    with open(os.path.join(tmp, "mpdt", "__init__.py"), "w") as f:
        f.write(f"__version__ = '{init_ver}'\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-qm", "init", "--allow-empty"], cwd=tmp, check=True)
    for t in tags:
        subprocess.run(["git", "tag", t], cwd=tmp, check=True)


class VersionGateTests(unittest.TestCase):
    def setUp(self) -> Any:
        self.gate = load_gate()

    def test_ok_bump_passes(self) -> None:
        # 0.2.4 > v0.2.4? No — must be strictly greater than highest tag.
        # Valid: files agree at 0.2.5, tags up to v0.2.4.
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.5", "0.2.5", ["v0.1.9", "v0.2.4"])
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 0, msg)

    def test_dual_file_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.2", "0.2.4", ["v0.2.4"])  # __init__ regressed
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("mismatch", msg.lower())

    def test_no_bump_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.4", "0.2.4", ["v0.2.4"])  # equal to tag = no bump
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("bump", msg.lower())

    def test_regression_below_tag_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.3", "0.2.3", ["v0.2.4"])
            rc, _msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)

    def test_real_repo_self_check(self) -> None:
        # 真实仓库自检：**不断言具体 rc**。
        #
        # 为什么（2026-10-21 R241）：这个用例原来硬编码"仓库当前 0.2.4 == tag v0.2.4
        # ⇒ 必须 FAIL"。但仓库版本一 bump（0.2.6），同一份代码就该 PASS ——
        # **断言了会变的状态**，版本一动测试就假失败。
        #
        # 真正该锁的是 gate 的**契约**：
        #   1) 真实仓库上不抛未捕获异常；
        #   2) 消息里必须点名两个文件位置（不论走哪个分支），否则失败不可操作；
        #   3) 真实仓库的两个文件版本必须一致（否则 gate 报的错会误导）。
        # NOTE: shallow checkouts (unit-test job, fetch-depth=1) have no tags —
        # highest_tag exits 2 there; degrade to skip rather than error (the
        # version-gate CI job runs the same path with full tags). Environment-
        # dependent test preconditions must skip, not fail (T22/T37 family).
        try:
            rc, msg = self.gate.check(REPO)
        except SystemExit as e:
            if e.code == 2:
                self.skipTest("shallow checkout without tags — covered by version-gate CI job")
            raise
        self.assertIn(rc, (0, 1), f"gate 只能 OK(0) 或 需要 bump(1)，实际 {rc}")
        if rc == 1:
            # 失败时消息必须可操作 ⇒ 点名两个文件
            self.assertIn("pyproject", msg)
            self.assertIn("__init__", msg)
        # 无论成败：两个文件的版本号必须一致（不一致 = gate 的报错会误导）
        repo = Path(REPO)
        pp = (repo / "pyproject.toml").read_text(encoding="utf-8")
        it = (repo / "mpdt" / "__init__.py").read_text(encoding="utf-8")
        import re as _re

        v1 = _re.search(r'version\s*=\s*"([^"]+)"', pp)
        # 2026-10-22（R68）：原正则只认单引号。ruff format 把 __version__ 改成双引号后
        # 这条测试假失败 —— **它断言的是引号风格，不是契约**。
        # 真正的契约是"两个文件版本一致"（下面一行），引号由格式化器决定。
        v2 = _re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", it)
        self.assertIsNotNone(v1, "pyproject.toml 里找不到 version")
        self.assertIsNotNone(v2, "mpdt/__init__.py 里找不到 __version__")
        self.assertEqual(
            v1.group(1), v2.group(1), "pyproject 与 __init__ 版本不一致 ⇒ gate 报错会误导"
        )

    def test_semver_compare_not_lexicographic(self) -> None:
        # 0.10.0 must compare greater than 0.9.9 (string compare would say "0.10" < "0.9")
        self.assertGreater(self.gate.parse_semver("0.10.0"), self.gate.parse_semver("0.9.9"))
        # tags without 'v' prefix and non-semver tags must be tolerated
        self.assertEqual(self.gate.parse_semver("1.2.3"), (1, 2, 3))


class TagVersionMatchTests(unittest.TestCase):
    """推 tag 发版时 tag 名必须等于版本号（R68）。

    背景：`publish.yml` 由 `push tag v*` 触发，包从**源码树**构建，tag 是另一个对象。
    推 `v0.2.7` 到一棵版本写着 0.2.8 的树上，会发布 0.2.8 而标签写着 v0.2.7 ——
    产物和标签对不上，而链路上没有任何其它环节会发现（PyPI 只认包里的版本号）。
    """

    def setUp(self) -> Any:
        self.gate = load_gate()

    def test_tag_matches_version_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.7", "0.2.7", ["v0.2.6", "v0.2.7"])
            rc, msg = self.gate.check(str(tmp), allow_eq=True, expect_tag="v0.2.7")
            self.assertEqual(rc, 0, msg)

    def test_tag_newer_than_version_fails(self) -> None:
        """tag 说 0.2.8，版本文件还停在 0.2.7 ⇒ 会发错版本，必须拦。"""
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.7", "0.2.7", ["v0.2.6", "v0.2.8"])
            rc, msg = self.gate.check(str(tmp), allow_eq=True, expect_tag="v0.2.8")
            self.assertEqual(rc, 1)
            self.assertIn("TAG/VERSION MISMATCH", msg)

    def test_version_newer_than_tag_fails(self) -> None:
        """版本已经 0.2.9，却推 v0.2.8 ⇒ 同样不一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.9", "0.2.9", ["v0.2.8"])
            rc, msg = self.gate.check(str(tmp), allow_eq=True, expect_tag="v0.2.8")
            self.assertEqual(rc, 1)
            self.assertIn("TAG/VERSION MISMATCH", msg)

    def test_non_semver_tag_is_tool_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.7", "0.2.7", ["v0.2.7"])
            rc, msg = self.gate.check(str(tmp), allow_eq=True, expect_tag="release-latest")
            self.assertEqual(rc, 2)
            self.assertIn("not semver", msg)

    def test_no_expect_tag_skips_the_check(self) -> None:
        """PR 场景不传 expect_tag ⇒ 不做 tag/版本一致性检查（否则 PR 永远红）。"""
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.7", "0.2.7", ["v0.2.6"])
            rc, msg = self.gate.check(str(tmp), allow_eq=True, expect_tag=None)
            self.assertEqual(rc, 0, msg)


if __name__ == "__main__":
    unittest.main()
