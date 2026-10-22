"""Tests for pypi_stats.py — watermark diff + first-run baseline + JSON fetch.

这些是 mock-only 测试 — 不真的连 pypistats.org (避免网络依赖)，保证 hermes 离线可跑。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Add tools/ to import
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import pypi_stats  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_recent_keys(self) -> None:
        d = {"last_day": 100, "last_week": 700, "last_month": 3000}
        norm = pypi_stats._normalize_recent(d)
        self.assertEqual(norm, {"last_day": 100, "last_week": 700, "last_month": 3000})

    def test_recent_missing(self) -> None:
        norm = pypi_stats._normalize_recent({})
        self.assertEqual(norm, {"last_day": 0, "last_week": 0, "last_month": 0})

    def test_overall_to_buckets(self) -> None:
        data = [
            {"category": "with_mirrors", "date": "2026-10-17", "downloads": 929},
            {"category": "without_mirrors", "date": "2026-10-18", "downloads": 158},
        ]
        b = pypi_stats._normalize_overall(data)
        self.assertEqual(b["with_mirrors"]["2026-10-17"], 929)
        self.assertEqual(b["without_mirrors"]["2026-10-18"], 158)


class TestWatermark(unittest.TestCase):
    def setUp(self) -> Any:
        self.tmpdir = tempfile.mkdtemp()
        self.wp = os.path.join(self.tmpdir, "pypi-mpdt.json")

    def tearDown(self) -> Any:
        if os.path.exists(self.wp):
            os.remove(self.wp)
        os.rmdir(self.tmpdir)

    def test_first_run_writes_baseline(self) -> None:
        self.assertIsNone(pypi_stats._read_watermark(self.wp))
        payload = {"recent": {"last_day": 1, "last_week": 2, "last_month": 3}}
        pypi_stats._write_watermark(self.wp, payload)
        got = pypi_stats._read_watermark(self.wp)
        self.assertEqual(got["recent"]["last_day"], 1)

    def test_check_no_delta_silent(self) -> None:
        """When prev == new and watermark exists, check should print nothing."""
        wm = {"recent": {"last_day": 100, "last_week": 700, "last_month": 3000}, "overall": {}}
        pypi_stats._write_watermark(self.wp, wm)
        with (
            patch.object(pypi_stats, "fetch_recent") as m1,
            patch.object(pypi_stats, "fetch_overall") as m2,
            patch("sys.argv", ["pypi_stats", "--package", "mpdt", "check"]),
        ):
            m1.return_value = {"data": {"last_day": 100, "last_week": 700, "last_month": 3000}}
            m2.return_value = {"data": []}
            rc = pypi_stats.cmd_check(pypi_stats_main_args())
            self.assertEqual(rc, 0)
            # print nothing = captured stdout empty
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                pypi_stats.cmd_check(pypi_stats_main_args())
            self.assertEqual(buf.getvalue(), "")


def pypi_stats_main_args() -> Any:
    """Build a fake argparse Namespace like cmd_check expects."""
    import argparse

    ns = argparse.Namespace()
    ns.package = "mpdt"
    ns.state_dir = "/tmp/_non_existent"
    ns.all = False
    return ns


if __name__ == "__main__":
    unittest.main()
