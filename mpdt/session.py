"""CDP 会话：连接本机已登录 Chrome（127.0.0.1:9222）的 mp.weixin tab，
token 自动刷新（从页面 URL 正则，FR2）；请求限速 ≥1s。

设计决策（03-technical-design §1.2）：直连 HTTP + 复用浏览器登录态，
不做 Selenium/Playwright。websocket-client 为系统级依赖。
"""

import json
import re
import time
import urllib.request

from .errors import AuthError

ANALYSIS_URL = "https://mp.weixin.qq.com/misc/appmsganalysis?action=all&lang=zh_CN"


class Session:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = cfg.get("token") or ""
        self.fingerprint = cfg.get("fingerprint") or ""
        self._last_req = 0.0
        self._ws = None
        self._mid = 0
        self._tab_url = ""

    # ---- CDP 基础 ----
    def _connect(self):
        if self._ws is not None:
            return
        import websocket

        try:
            tabs = json.load(urllib.request.urlopen(self.cfg["chrome_cdp"] + "/json", timeout=10))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AuthError(
                f"无法连接 Chrome CDP（{self.cfg['chrome_cdp']}）：{e}\n"
                "请先以 --remote-debugging-port=9222 启动 Chrome 并登录 mp.weixin.qq.com"
            ) from e
        page = [t for t in tabs if t.get("type") == "page" and "mp.weixin" in t.get("url", "")]
        if not page:
            raise AuthError(
                "CDP 9222 上没有 mp.weixin 标签页。"
                "请先用 --cdp 启动参数所指 Chrome 打开并登录 mp.weixin.qq.com"
            )
        self._ws = websocket.create_connection(page[0]["webSocketDebuggerUrl"], timeout=90)
        self._tab_url = page[0].get("url", "")
        self._cmd("Page.enable")

    def _cmd(self, method, **params):
        self._mid += 1
        self._ws.send(json.dumps({"id": self._mid, "method": method, "params": params}))
        self._ws.settimeout(60)
        while True:
            m = json.loads(self._ws.recv())
            if m.get("id") == self._mid:
                return m

    def _eval(self, js):
        r = self._cmd("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("result", {}).get("value")

    # ---- token 刷新（FR2）----
    def refresh_token(self) -> str:
        """导航到分析页，从落地 URL 正则取新 token（已验证方案）。
        种子优先级：cfg token > 当前 tab URL 内 token（登录后台的 tab URL 自带）。"""
        seed = self.token
        if not seed:
            m_tab = re.search(r"token=(\d+)", self._tab_url or "")
            seed = m_tab.group(1) if m_tab else ""
        self._connect()
        # 实测：分析页带无效 token 不重写 URL；首页 / 会重定向到 home?...&token=xxx
        self._cmd("Page.navigate", url="https://mp.weixin.qq.com/")
        time.sleep(8)
        href = self._eval("location.href") or ""
        m = re.search(r"token=(\d+)", href)
        if not m:
            raise AuthError(f"token 刷新失败：落地 URL 无 token（href={href[:120]}）")
        self.token = m.group(1)
        return self.token

    def ensure_token(self) -> str:
        if not self.token:
            self.refresh_token()
        return self.token

    # ---- 页内 fetch（登录态 cookie 自动附带）----
    def fetch_text(self, js) -> str:
        self._connect()
        self.throttle()
        v = self._eval(js)
        if v is None or v == "":
            raise AuthError("页面内 fetch 无返回（tab 可能已失效，重跑一次）")
        return v

    def throttle(self):
        wait = self.cfg.get("request_interval", 1.0) - (time.time() - self._last_req)
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()
