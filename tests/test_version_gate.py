"""Tests for tools/version_gate.py — dual-file version consistency + monotonic bump gate.

Refs: e1 R113 rule candidate — pyproject.toml and mpdt/__init__.py versions must
match AND be strictly greater than the highest existing git tag (semver).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "tools", "version_gate.py")


def load_gate():
    spec = importlib.util.spec_from_file_location("version_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_repo(tmp, init_ver, pyproject_ver, tags):
    """Create a minimal repo structure: pyproject.toml, mpdt/__init__.py, git tags."""
    os.makedirs(os.path.join(tmp, "mpdt"))
    with open(os.path.join(tmp, "pyproject.toml"), "w") as f:
        f.write('[project]\nname = "mpdt"\nversion = "%s"\n' % pyproject_ver)
    with open(os.path.join(tmp, "mpdt", "__init__.py"), "w") as f:
        f.write("__version__ = '%s'\n" % init_ver)
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-qm", "init", "--allow-empty"], cwd=tmp, check=True)
    for t in tags:
        subprocess.run(["git", "tag", t], cwd=tmp, check=True)


class VersionGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()

    def test_ok_bump_passes(self):
        # 0.2.4 > v0.2.4? No — must be strictly greater than highest tag.
        # Valid: files agree at 0.2.5, tags up to v0.2.4.
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.5", "0.2.5", ["v0.1.9", "v0.2.4"])
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 0, msg)

    def test_dual_file_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.2", "0.2.4", ["v0.2.4"])  # __init__ regressed
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("mismatch", msg.lower())

    def test_no_bump_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.4", "0.2.4", ["v0.2.4"])  # equal to tag = no bump
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("bump", msg.lower())

    def test_regression_below_tag_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo(tmp, "0.2.3", "0.2.3", ["v0.2.4"])
            rc, msg = self.gate.check(str(tmp))
            self.assertEqual(rc, 1)

    def test_real_repo_self_check(self):
        # The actual repo right now: files 0.2.4 == tag v0.2.4 → must FAIL with
        # "no bump" (correct: post-release HEAD without bump is exactly the state
        # the gate exists to flag). Message must name both file locations so the
        # failure is actionable regardless of which branch of the gate fires.
        rc, msg = self.gate.check(REPO)
        self.assertEqual(rc, 1)
        self.assertIn("pyproject", msg)
        self.assertIn("__init__", msg)

    def test_semver_compare_not_lexicographic(self):
        # 0.10.0 must compare greater than 0.9.9 (string compare would say "0.10" < "0.9")
        self.assertGreater(self.gate.parse_semver("0.10.0"), self.gate.parse_semver("0.9.9"))
        # tags without 'v' prefix and non-semver tags must be tolerated
        self.assertEqual(self.gate.parse_semver("1.2.3"), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
