"""session.py 覆盖率补测（R68）。

背景：质量门把覆盖率门槛设到 90%，而 session.py 当时只有 25.58%（48 条未覆盖）——
它是唯一和 Chrome CDP 打交道的模块，原来的测试直接跳过它。

这里不连真 Chrome：CDP 用 `urllib.request.urlopen`，websocket 用 `websocket.create_connection`，
两者都在 `unittest.mock` 下换成假对象。测的是**这个模块自己的分支**，不是网络。

覆盖的契约：
  1) `_connect`：已连接直接返回 / CDP 不可达报 AuthError / 没有 mp.weixin 标签页报 AuthError /
     成功时发 Page.enable
  2) `_cmd`：只返回 id 匹配的那一帧（CDP 会推无关事件）
  3) `_eval`：从三层嵌套里取 value
  4) `refresh_token`：cfg token 优先 / 无 cfg 时从 tab URL 取种子 / 落地 URL 无 token 报 AuthError
  5) `ensure_token`：空才刷新
  6) `fetch_text`：空返回报 AuthError；正常返回原字符串
  7) `throttle`：间隔未到要等，到点不等
"""

import json
import sys
import types
import unittest
from typing import Any
from unittest import mock

from mpdt import session
from mpdt.errors import AuthError


class _FakeWS:
    """假的 CDP websocket：send 记下帧，recv 按脚本返回。"""

    def __init__(self, frames: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._frames = list(frames or [])
        self.timeout: float | None = None

    def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def recv(self) -> str:
        if not self._frames:
            raise RuntimeError("recv 无更多帧")
        return json.dumps(self._frames.pop(0))

    def settimeout(self, t: float) -> None:
        self.timeout = t


def _cfg(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "chrome_cdp": "http://127.0.0.1:9222",
        "token": "111",
        "request_interval": 0.0,
    }
    base.update(over)
    return base


class _Resp:
    """urlopen 的最小替身。"""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _fake_websocket(ws: _FakeWS) -> types.ModuleType:
    mod = types.ModuleType("websocket")
    mod.create_connection = lambda url, timeout=0: ws  # type: ignore[attr-defined]
    return mod


class SessionConnectTests(unittest.TestCase):
    def test_connect_is_idempotent(self) -> None:
        s = session.Session(_cfg())
        s._ws = "already"  # type: ignore[assignment]
        s._connect()  # 不应再走 urlopen
        self.assertEqual(s._ws, "already")

    def test_connect_cdp_unreachable(self) -> None:
        s = session.Session(_cfg())
        with (
            mock.patch.object(session.urllib.request, "urlopen", side_effect=OSError("refused")),
            self.assertRaises(AuthError) as ctx,
        ):
            s._connect()
        self.assertIn("无法连接 Chrome CDP", str(ctx.exception))

    def test_connect_no_weixin_tab(self) -> None:
        s = session.Session(_cfg())
        tabs = [{"type": "page", "url": "https://example.com"}]
        with (
            mock.patch.object(session.urllib.request, "urlopen", return_value=_Resp(tabs)),
            self.assertRaises(AuthError) as ctx,
        ):
            s._connect()
        self.assertIn("没有 mp.weixin 标签页", str(ctx.exception))

    def test_connect_success_sends_page_enable(self) -> None:
        s = session.Session(_cfg())
        tabs = [
            {
                "type": "page",
                "url": "https://mp.weixin.qq.com/x?token=9",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1",
            }
        ]
        ws = _FakeWS(frames=[{"id": 1, "result": {}}])
        with (
            mock.patch.object(session.urllib.request, "urlopen", return_value=_Resp(tabs)),
            mock.patch.dict(sys.modules, {"websocket": _fake_websocket(ws)}),
        ):
            s._connect()
        self.assertIsNotNone(s._ws)
        self.assertIn("token=9", s._tab_url)
        self.assertEqual(ws.sent[0]["method"], "Page.enable")


class SessionCmdTests(unittest.TestCase):
    def test_cmd_skips_unrelated_frames(self) -> None:
        s = session.Session(_cfg())
        ws = _FakeWS(
            frames=[
                {"method": "Page.loadEventFired"},  # 无关事件，必须跳过
                {"id": 1, "result": {"ok": True}},
            ]
        )
        s._ws = ws  # type: ignore[assignment]
        out = s._cmd("Page.enable")
        self.assertEqual(out, {"id": 1, "result": {"ok": True}})
        self.assertEqual(ws.sent[0]["params"], {})
        self.assertEqual(ws.timeout, 60)

    def test_eval_unwraps_value(self) -> None:
        s = session.Session(_cfg())
        s._ws = _FakeWS(frames=[{"id": 1, "result": {"result": {"value": "hello"}}}])  # type: ignore[assignment]
        self.assertEqual(s._eval("1+1"), "hello")

    def test_eval_missing_value_is_none(self) -> None:
        s = session.Session(_cfg())
        s._ws = _FakeWS(frames=[{"id": 1, "result": {}}])  # type: ignore[assignment]
        self.assertIsNone(s._eval("x"))


class RefreshTokenTests(unittest.TestCase):
    def _patched(self, ws: _FakeWS, tabs: list[dict[str, Any]]) -> Any:
        return (
            mock.patch.object(session.urllib.request, "urlopen", return_value=_Resp(tabs)),
            mock.patch.dict(sys.modules, {"websocket": _fake_websocket(ws)}),
            mock.patch.object(session.time, "sleep", return_value=None),
        )

    def test_refresh_uses_cfg_token_and_reads_href(self) -> None:
        s = session.Session(_cfg(token="111"))
        tabs = [
            {
                "type": "page",
                "url": "https://mp.weixin.qq.com/x?token=111",
                "webSocketDebuggerUrl": "ws://x",
            }
        ]
        ws = _FakeWS(
            frames=[
                {"id": 1, "result": {}},  # Page.enable
                {"id": 2, "result": {}},  # Page.navigate
                {
                    "id": 3,
                    "result": {"result": {"value": "https://mp.weixin.qq.com/home?token=222"}},
                },
            ]
        )
        p1, p2, p3 = self._patched(ws, tabs)
        with p1, p2, p3:
            self.assertEqual(s.refresh_token(), "222")
        self.assertEqual(s.token, "222")
        self.assertEqual(ws.sent[1]["method"], "Page.navigate")

    def test_refresh_falls_back_to_tab_url_token(self) -> None:
        s = session.Session(_cfg(token=""))
        s._tab_url = "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=555"
        tabs = [{"type": "page", "url": s._tab_url, "webSocketDebuggerUrl": "ws://x"}]
        ws = _FakeWS(
            frames=[
                {"id": 1, "result": {}},
                {"id": 2, "result": {}},
                {
                    "id": 3,
                    "result": {"result": {"value": "https://mp.weixin.qq.com/home?token=777"}},
                },
            ]
        )
        p1, p2, p3 = self._patched(ws, tabs)
        with p1, p2, p3:
            self.assertEqual(s.refresh_token(), "777")

    def test_refresh_without_token_in_href_raises(self) -> None:
        s = session.Session(_cfg(token="111"))
        tabs = [
            {
                "type": "page",
                "url": "https://mp.weixin.qq.com/x?token=111",
                "webSocketDebuggerUrl": "ws://x",
            }
        ]
        ws = _FakeWS(
            frames=[
                {"id": 1, "result": {}},
                {"id": 2, "result": {}},
                {"id": 3, "result": {"result": {"value": "https://mp.weixin.qq.com/login"}}},
            ]
        )
        p1, p2, p3 = self._patched(ws, tabs)
        with p1, p2, p3, self.assertRaises(AuthError) as ctx:
            s.refresh_token()
        self.assertIn("token 刷新失败", str(ctx.exception))


class EnsureTokenTests(unittest.TestCase):
    def test_ensure_token_returns_existing_without_refresh(self) -> None:
        s = session.Session(_cfg(token="999"))
        self.assertEqual(s.ensure_token(), "999")

    def test_ensure_token_refreshes_when_empty(self) -> None:
        # ensure_token 返回的是 self.token，不是 refresh_token 的返回值 ——
        # 真 refresh_token 会自己写 self.token，所以替身也必须写，否则测试测的是假契约。
        s = session.Session(_cfg(token=""))

        def _fake_refresh(self_: session.Session) -> str:
            self_.token = "321"
            return self_.token

        with mock.patch.object(session.Session, "refresh_token", _fake_refresh):
            self.assertEqual(s.ensure_token(), "321")


class FetchTextTests(unittest.TestCase):
    def test_fetch_text_returns_value(self) -> None:
        s = session.Session(_cfg(token="1"))
        s._connect = lambda: None  # type: ignore[method-assign]
        s._eval = lambda js: '{"a":1}'  # type: ignore[method-assign]
        self.assertEqual(s.fetch_text("fetch()"), '{"a":1}')

    def test_fetch_text_empty_raises(self) -> None:
        s = session.Session(_cfg(token="1"))
        s._connect = lambda: None  # type: ignore[method-assign]
        s._eval = lambda js: ""  # type: ignore[method-assign]
        with self.assertRaises(AuthError) as ctx:
            s.fetch_text("fetch()")
        self.assertIn("页面内 fetch 无返回", str(ctx.exception))


class ThrottleTests(unittest.TestCase):
    def test_throttle_sleeps_when_interval_not_elapsed(self) -> None:
        s = session.Session(_cfg(request_interval=10.0))
        s._last_req = session.time.time()  # 刚刚请求过 ⇒ 必须等
        slept: list[float] = []
        with mock.patch.object(session.time, "sleep", side_effect=slept.append):
            s.throttle()
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)

    def test_throttle_no_sleep_when_interval_elapsed(self) -> None:
        s = session.Session(_cfg(request_interval=0.0))
        s._last_req = 0.0
        slept: list[float] = []
        with mock.patch.object(session.time, "sleep", side_effect=slept.append):
            s.throttle()
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
