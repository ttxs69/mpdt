"""投递链路的**失败路径**与本地状态文件。

SMTP 接受 ≠ 送达（R239/R68）：这个文件测的全是「怎么知道自己没送到」——
IMAP 回查成功/失败、白名单缺失、放行留痕、认证失败的翻译。
全部用替身，不真的发信。
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mpdt import push

imaplib = importlib.import_module("imaplib")

smtplib = importlib.import_module("smtplib")


class _FakeIMAP:
    """最小 IMAP 替身：search 返回 3 个 id，fetch 返回假头部。"""

    def __init__(self, headers: list[bytes], fail_login: bool = False) -> None:
        self.headers = headers
        self.fail_login = fail_login
        self.logged_out = False
        self.selected: str | None = None

    def login(self, user: str, password: str) -> None:
        if self.fail_login:
            raise OSError("auth failed")

    def select(self, box: str) -> None:
        self.selected = box

    def search(self, charset: str, criteria: str) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(i).encode() for i in range(1, len(self.headers) + 1))]

    def fetch(self, idx: bytes, what: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        n = int(idx) - 1
        return "OK", [(b"1 (BODY[] {1}", self.headers[n])]

    def logout(self) -> None:
        self.logged_out = True


def _header(subject: str) -> bytes:
    return (f"Subject: {subject}\r\nFrom: a@b.c\r\nTo: d@e.f\r\n\r\n").encode()


class PushPathsBase(unittest.TestCase):
    """把 HOME 指到临时目录：push 的状态文件都在 ~/.mpdt。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"HOME": self._tmp.name})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    @property
    def mpdt_dir(self) -> Path:
        return Path(self._tmp.name) / ".mpdt"


class TestLocalState(PushPathsBase):
    def test_sent_db_path_creates_state_dir(self) -> None:
        p = push._sent_db_path()
        self.assertEqual(p.parent, self.mpdt_dir)
        self.assertTrue(p.parent.is_dir())
        self.assertEqual(p.name, "pushed.sqlite3")

    def test_allowlist_path_does_not_create_anything(self) -> None:
        """读路径不写盘：只是探一下白名单在不在。"""
        p = push._allowlist_path()
        self.assertFalse(p.exists())
        self.assertFalse(self.mpdt_dir.exists())

    def test_missing_allowlist_is_empty(self) -> None:
        self.assertEqual(push._load_allowlist(), [])

    def test_malformed_allowlist_is_empty_not_fatal(self) -> None:
        self.mpdt_dir.mkdir(parents=True, exist_ok=True)
        push._allowlist_path().write_text("{broken", encoding="utf-8")
        self.assertEqual(push._load_allowlist(), [])

    def test_allowlist_is_lowercased_and_stripped(self) -> None:
        self.mpdt_dir.mkdir(parents=True, exist_ok=True)
        push._allowlist_path().write_text(
            json.dumps({"allow": ["  A@Example.COM ", "", "b@example.com"]}), encoding="utf-8"
        )
        self.assertEqual(push._load_allowlist(), ["a@example.com", "b@example.com"])

    def test_unlisted_release_leaves_an_audit_line(self) -> None:
        push._log_unlisted("cold@lead.example", "2026-W43")
        log = self.mpdt_dir / "push_unlisted.log"
        line = log.read_text(encoding="utf-8")
        self.assertIn("2026-W43", line)
        self.assertIn("cold@lead.example", line)
        self.assertEqual(len(line.strip().splitlines()), 1)


class TestAssertAllowed(PushPathsBase):
    def test_self_address_needs_no_log(self) -> None:
        """本人地址来自环境变量（push._self_addresses 只认 env / ~/.hermes/.env）。"""
        with mock.patch.dict(os.environ, {"EMAIL_ADDRESS": "me@example.com"}):
            push._assert_allowed("me@example.com", "2026-W43", {}, allow_unlisted=True)
        self.assertFalse((self.mpdt_dir / "push_unlisted.log").exists())

    def test_unlisted_with_release_flag_is_logged(self) -> None:
        push._assert_allowed("cold@lead.example", "2026-W43", {}, allow_unlisted=True)
        self.assertTrue((self.mpdt_dir / "push_unlisted.log").exists())

    def test_without_release_flag_it_raises(self) -> None:
        with self.assertRaises(push.PushError) as ctx:
            push._assert_allowed("cold@lead.example", "2026-W43", {}, allow_unlisted=False)
        self.assertIn("push_allowlist.json", str(ctx.exception))


class TestHtmlInline(PushPathsBase):
    def test_missing_file_gives_empty_string(self) -> None:
        self.assertEqual(push._html_inline(Path(self._tmp.name) / "nope.html"), "")

    def test_only_body_is_kept(self) -> None:
        p = Path(self._tmp.name) / "r.html"
        p.write_text("<html><head><style>body{}</style></head><body><h1>周报</h1></body></html>")
        out = push._html_inline(p)
        self.assertTrue(out.startswith("<body"))
        self.assertIn("周报", out)
        self.assertNotIn("<style>", out)

    def test_html_without_body_is_returned_whole(self) -> None:
        p = Path(self._tmp.name) / "r.html"
        p.write_text("<p>裸片段</p>")
        self.assertEqual(push._html_inline(p), "<p>裸片段</p>")


class TestImapHeaders(PushPathsBase):
    def test_fetch_reads_headers_and_logs_out(self) -> None:
        fake = _FakeIMAP([_header("a"), _header("b")])
        with mock.patch.object(imaplib, "IMAP4_SSL", return_value=fake):
            raws = push._imap_recent_headers("imap.example.com", 993, "u", "p")
        self.assertEqual(len(raws), 2)
        self.assertEqual(fake.selected, "INBOX")
        self.assertTrue(fake.logged_out)

    def test_logout_happens_even_when_fetch_explodes(self) -> None:
        fake = _FakeIMAP([_header("a")])
        patcher = mock.patch.object(fake, "fetch", side_effect=OSError("socket died"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(imaplib, "IMAP4_SSL", return_value=fake), self.assertRaises(OSError):
            push._imap_recent_headers("imap.example.com", 993, "u", "p")
        self.assertTrue(fake.logged_out, "异常路径也必须 logout，否则连接泄漏")


class TestVerifyDelivery(PushPathsBase):
    def test_missing_credentials_is_not_verified(self) -> None:
        out = push.verify_delivery("a@b.c", subject_contains="周报")
        self.assertFalse(out["delivered"])
        self.assertIn("缺少 IMAP 凭据", out["error"])

    def test_found_subject_is_delivered(self) -> None:
        with mock.patch.object(
            push,
            "_imap_recent_headers",
            return_value=[_header("别的"), _header("公众号数据周报 2026-W43")],
        ):
            out = push.verify_delivery(
                "a@b.c",
                subject_contains="2026-W43",
                imap_host="h",
                imap_user="u",
                imap_password="p",
            )
        self.assertTrue(out["delivered"])
        self.assertEqual(out["checked"], 2)

    def test_not_found_is_not_delivered(self) -> None:
        with mock.patch.object(push, "_imap_recent_headers", return_value=[_header("别的")]):
            out = push.verify_delivery(
                "a@b.c",
                subject_contains="2026-W43",
                imap_host="h",
                imap_user="u",
                imap_password="p",
            )
        self.assertFalse(out["delivered"])
        self.assertIn("未找到主题含", out["error"])

    def test_imap_exception_is_reported_not_raised(self) -> None:
        with mock.patch.object(push, "_imap_recent_headers", side_effect=OSError("down")):
            out = push.verify_delivery(
                "a@b.c", subject_contains="x", imap_host="h", imap_user="u", imap_password="p"
            )
        self.assertFalse(out["delivered"])
        self.assertIn("IMAP 校验失败", out["error"])


class TestDeliverTranslatesErrors(PushPathsBase):
    """SMTP 的错误必须变成「下一步怎么做」，而不是把栈丢给用户。"""

    def _cfg(self) -> dict[str, Any]:
        return {
            "host": "smtp.example.com",
            "port": 465,
            "user": "u",
            "password": "p",
            "from": "u@e.c",
        }

    def test_auth_failure_says_which_env_vars_to_check(self) -> None:
        err = smtplib.SMTPAuthenticationError(535, b"bad")
        with (
            mock.patch.object(smtplib, "SMTP_SSL", side_effect=err),
            self.assertRaises(push.PushError) as ctx,
        ):
            push._deliver(self._cfg(), mock.Mock())
        msg = str(ctx.exception)
        self.assertIn("认证失败", msg)
        self.assertIn(push.ENV_KEYS["password"], msg)

    def test_network_failure_says_retry_and_does_not_swallow(self) -> None:
        with (
            mock.patch.object(smtplib, "SMTP_SSL", side_effect=OSError("connection refused")),
            self.assertRaises(push.PushError) as ctx,
        ):
            push._deliver(self._cfg(), mock.Mock())
        msg = str(ctx.exception)
        self.assertIn("推送失败", msg)
        self.assertIn("OSError", msg)


if __name__ == "__main__":
    unittest.main()
