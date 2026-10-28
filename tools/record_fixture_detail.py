#!/usr/bin/env python3
"""record_fixture_detail.py — 录制「单篇图文分析页」（渠道页，FR3②）真实数据为 fixture。

端点（2026-10-17 实测逆向，R28）：
  GET /misc/appmsganalysis?action=detailpage&msgid=<appmsgid>_<itemidx>
      &publish_date=<YYYY-MM-DD>&type=int&pageVersion=1&token=..&lang=zh_CN
  → HTML 内嵌数据脚本：window.wx.cgiData = { articleData:{...}, articleSummaryData:{...}, detailData:{...} }
    · articleData.article_data_new: 完读率(finished_read_pv_ratio)/平均阅读时长/read_uv/share_uv/互动计数
    · articleSummaryData.list: 逐日 × 渠道(scene) read_user/share_user（9999=全部）
  渠道 scene 映射来源：页面 JS 包 mass_detail_new.*.js 的 sceneTags（primary source，实测录制）。

安全：**只提取数据 JSON 对象重建脚本文本**（页面其它 <script> 含 ticket/uin 等会话字段，
整段 HTML 落盘会泄漏凭据 —— 首版踩坑，见 log R28）。只读；每篇间隔 ≥2s。
用法: python3 record_fixture_detail.py [msgid:itemidx:publish_date ...]
"""

import json
import os
import re
import sys
import time
import urllib.request
from typing import Any

import websocket

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")
os.makedirs(OUT, exist_ok=True)

DEFAULT_TARGETS = [
    ("2247485219", 1, "2026-10-16"),  # W42 主文（当日口径）
    ("2247485121", 1, "2026-10-12"),  # 与 datacube tmpl=17 finish_read_rate 交叉校验
    ("2247484945", 1, "2026-09-23"),  # 2026-09 高阅读文（渠道分布多样）
]


def _match_brace(text: str, start: int) -> Any:
    """从 text[start] 的 '{' 起，返回配对 '}' 的下标；不配对返回 None。

    字符串里的括号和转义不参与配对 —— 微信返回的 JSON 里标题/正文含大量引号与
    大括号，按字符数括号会切错位置。
    """
    depth = 0
    in_str = False
    esc = False
    for k in range(start, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return k
    return None


def _json_after(text: str, key: str) -> Any:
    """取 `key:` 之后第一个平衡括号 JSON 文本（字符串内括号/转义安全）。"""
    i = text.find(key + ":")
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    end = _match_brace(text, j)
    return None if end is None else text[j : end + 1]


def extract_cgi_data(html: str) -> Any:
    """返回 (fixture_text, 解析后 dict)；数据脚本定位用 articleData 做锚（页面另有空 cgiData={}）。"""
    if "articleData" not in html:
        return None, None
    parts = {}
    for key in ("articleData", "articleSummaryData", "detailData"):
        seg = _json_after(html, key)
        if seg is None:
            return None, None
        try:
            parts[key] = json.loads(seg)
        except ValueError:
            return None, None
    body = ",\n    ".join(
        f"{k}:\n      {json.dumps(parts[k], ensure_ascii=False)}"
        for k in ("articleData", "articleSummaryData", "detailData")
    )
    return f"window.wx.cgiData = {{\n    {body}\n  }};\n", parts


def main() -> int:
    targets: list[tuple[str, int, str]] = []
    for a in sys.argv[1:]:
        mid, idx, pub = a.split(":")
        targets.append((mid, int(idx), pub))
    targets = targets or DEFAULT_TARGETS

    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=10))
    page = [t for t in tabs if t["type"] == "page" and "mp.weixin" in t.get("url", "")]
    if not page:
        sys.exit("no mp.weixin tab")
    ws = websocket.create_connection(
        page[0]["webSocketDebuggerUrl"], timeout=120, max_size=200 * 1024 * 1024
    )
    mid_n = [0]

    def run_js(js: Any) -> Any:
        mid_n[0] += 1
        ws.send(
            json.dumps(
                {
                    "id": mid_n[0],
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True, "awaitPromise": True},
                }
            )
        )
        ws.settimeout(90)
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid_n[0]:
                return m.get("result", {}).get("result", {}).get("value")

    tok_m = re.search(r"token=(\d+)", page[0].get("url", ""))
    tok = tok_m.group(1) if tok_m else ""
    if not tok:
        sys.exit("no token in tab url")
    print("token:", tok[:4] + "...")

    for t_mid, t_idx, t_pub in targets:
        js = f"""
        (async () => {{
          const res = await fetch('/misc/appmsganalysis?action=detailpage&msgid={t_mid}_{t_idx}&publish_date={t_pub}&type=int&pageVersion=1&token={tok}&lang=zh_CN', {{credentials:'include'}});
          const t = await res.text();
          return res.status + '|' + t;
        }})()"""
        raw = run_js(js) or ""
        status, _, html = raw.partition("|")
        fx, parts = extract_cgi_data(html)
        if status != "200" or fx is None:
            print(f"SKIP {t_mid}_{t_idx} status={status} parsed={fx is not None} len={len(html)}")
            continue
        path = os.path.join(OUT, f"article_detail_{t_mid}_{t_idx}.html")
        with open(path, "w") as f:
            f.write(fx)
        title_hint = parts["articleData"].get("title", "")[:18]
        read_uv = (parts["articleData"].get("article_data_new") or {}).get("read_uv")
        print(
            f"OK {t_mid}_{t_idx} ({t_pub}) -> {len(fx)} bytes  title={title_hint!r}  read_uv={read_uv}"
        )
        time.sleep(2)
    return 0


if __name__ == "__main__":
    main()
