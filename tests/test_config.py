"""config 单元测试（T2）：save 原子写+600 权限，load 缺省合并+损坏/缺失报错。
CONFIG_DIR/CONFIG_PATH 是模块级常量 → 每用例以 monkeypatch 风格替换到临时目录并复原。
运行：python3 tests/test_config.py 或 unittest discover  （零第三方依赖）
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


from mpdt import config  # noqa: E402
from mpdt.errors import ConfigError  # noqa: E402


class ConfigTmpDir(unittest.TestCase):
    """把 config.CONFIG_DIR / CONFIG_PATH / DEFAULTS['db_path'] 指到临时目录。"""

    def setUp(self) -> Any:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (config.CONFIG_DIR, config.CONFIG_PATH, config.DEFAULTS["db_path"])
        config.CONFIG_DIR = self._tmp.name
        config.CONFIG_PATH = os.path.join(self._tmp.name, "config.json")
        config.DEFAULTS["db_path"] = os.path.join(self._tmp.name, "mpdt.sqlite3")

    def tearDown(self) -> Any:
        config.CONFIG_DIR, config.CONFIG_PATH, dbp = self._saved
        config.DEFAULTS["db_path"] = dbp
        self._tmp.cleanup()


class TestSave(ConfigTmpDir):
    def test_write_then_load_roundtrip(self) -> None:
        cfg = {
            "fingerprint": "fp1",
            "request_interval": 2.0,
            "db_path": os.path.join(self._tmp.name, "x.sqlite3"),
        }
        config.save(cfg)
        self.assertTrue(os.path.exists(config.CONFIG_PATH))
        loaded = config.load()
        self.assertEqual(loaded["fingerprint"], "fp1")
        self.assertEqual(loaded["request_interval"], 2.0)
        self.assertEqual(loaded["db_path"], cfg["db_path"])
        self.assertEqual(loaded, dict(cfg, chrome_cdp="http://127.0.0.1:9222"))  # 补默认键

    def test_mode_600(self) -> None:
        config.save({"fingerprint": ""})
        mode = stat.S_IMODE(os.stat(config.CONFIG_PATH).st_mode)
        self.assertEqual(mode, 0o600, "凭据文件权限必须 600")

    def test_no_tmp_left_behind(self) -> None:
        config.save({"fingerprint": ""})
        self.assertEqual(os.listdir(self._tmp.name), ["config.json"])  # .tmp 已原子替换

    def test_overwrite_existing(self) -> None:
        config.save({"fingerprint": "a"})
        config.save({"fingerprint": "b", "extra": 1})
        loaded = config.load()
        self.assertEqual(loaded["fingerprint"], "b")
        self.assertEqual(loaded["extra"], 1)


class TestLoad(ConfigTmpDir):
    def test_missing_raises_config_error_exit2(self) -> None:
        with self.assertRaises(ConfigError) as cm:
            config.load()
        self.assertEqual(cm.exception.exit_code, 2)

    def test_corrupt_json_raises_config_error(self) -> None:
        Path(config.CONFIG_PATH).write_text("{ not json !!!", encoding="utf-8")
        with self.assertRaises(ConfigError):
            config.load()

    def test_defaults_merged(self) -> None:
        # 只写部分键 → load 合并 DEFAULTS（chrome_cdp / request_interval 补齐）
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": "fp2"}, f)
        cfg = config.load()
        self.assertEqual(cfg["fingerprint"], "fp2")
        self.assertEqual(cfg["chrome_cdp"], "http://127.0.0.1:9222")
        self.assertEqual(cfg["request_interval"], 1.0)
        self.assertEqual(cfg["db_path"], config.DEFAULTS["db_path"])

    def test_user_value_overrides_default(self) -> None:
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"request_interval": 5.0}, f)
        self.assertEqual(config.load()["request_interval"], 5.0)


if __name__ == "__main__":
    unittest.main()
