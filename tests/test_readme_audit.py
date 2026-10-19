"""Tests for readme_audit.py — mock network, assert diff/ok flows."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import readme_audit  # noqa: E402


class TestFetchDesc(unittest.TestCase):
    def test_fetch_returns_description(self):
        with patch("urllib.request.urlopen") as m:
            fake_resp = MagicMock()
            fake_resp.read.return_value = json.dumps({
                "info": {"description": "# hello"}
            }).encode()
            fake_resp.__enter__ = lambda s: s
            fake_resp.__exit__ = MagicMock(return_value=False)
            m.return_value = fake_resp
            desc = readme_audit.fetch_pypi_desc("mpdt")
            self.assertEqual(desc, "# hello")


class TestMain(unittest.TestCase):
    def _write_readme(self, content):
        self.tmpdir = tempfile.mkdtemp()
        rd = Path(self.tmpdir) / "README.md"
        rd.write_text(content)
        return rd

    def tearDown(self):
        if hasattr(self, "tmpdir"):
            import shutil
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_match_returns_ok(self):
        self._write_readme("# identical content\n")
        with patch.object(readme_audit, "fetch_pypi_desc", return_value="# identical content\n"):
            with patch("sys.argv", ["readme_audit", "--repo", self.tmpdir]):
                rc = readme_audit.main()
                self.assertEqual(rc, 0)

    def test_diff_returns_1_strict(self):
        self._write_readme("# local v2\n")
        with patch.object(readme_audit, "fetch_pypi_desc", return_value="# remote v1\n"):
            with patch("sys.argv", ["readme_audit", "--repo", self.tmpdir, "--strict-diff"]):
                rc = readme_audit.main()
                self.assertEqual(rc, 1)

    def test_diff_returns_0_non_strict(self):
        self._write_readme("# local v2\n")
        with patch.object(readme_audit, "fetch_pypi_desc", return_value="# remote v1\n"):
            with patch("sys.argv", ["readme_audit", "--repo", self.tmpdir]):
                rc = readme_audit.main()
                self.assertEqual(rc, 0)

    def test_missing_readme_returns_4(self):
        self.tmpdir = tempfile.mkdtemp()
        with patch("sys.argv", ["readme_audit", "--repo", self.tmpdir]):
            rc = readme_audit.main()
            self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()
