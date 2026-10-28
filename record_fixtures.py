#!/usr/bin/env python3
"""record_fixtures.py — 从 CDP 9222 的 mp.weixin 已登录 tab 录制真实接口返回，存 tests/fixtures/。
只读；两条查询间隔 2s；fixture 中剥离 token/fingerprint 等会话字段。"""

import json
import os
import re
import sys
import time
import urllib.request

import websocket

OUT = os.path.join(os.path.dirname(__file__), "tests", "fixtures")
os.makedirs(OUT, exist_ok=True)

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
page = [t for t in tabs if t["type"] == "page" and "mp.weixin" in t.get("url", "")]
if not page:
    sys.exit("no mp.weixin tab")
ws = websocket.create_connection(page[0]["webSocketDebuggerUrl"], timeout=90)
mid = [0]


def cmd(method, **params):
    mid[0] += 1
    ws.send(json.dumps({"id": mid[0], "method": method, "params": params}))
    ws.settimeout(60)
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid[0]:
            return m


cmd("Page.enable")
href = (
    cmd("Runtime.evaluate", expression="location.href", returnByValue=True)
    .get("result", {})
    .get("result", {})
    .get("value", "")
)

mtok = re.search(r"token=(\d+)", href)
TOKEN = mtok.group(1) if mtok else ""
print("token from url:", TOKEN[:4] + "..." if TOKEN else "MISSING")

# fingerprint 从已验证工具读取（不硬编码第二份；fixture 落盘时剥离）
FP = ""
with open(os.path.expanduser("~/moneymaker/tools/mp-datacube.py"), encoding="utf-8") as src:
    for line in src:
        mfp = re.match(r"FINGERPRINT\s*=\s*'([0-9a-f]+)'", line)
        if mfp:
            FP = mfp.group(1)
            break
print("fingerprint loaded:", FP[:6] + "..." if FP else "MISSING")


def run_js(js):
    r = cmd("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
    return r.get("result", {}).get("result", {}).get("value", "")


# fixture 1: appmsgpublish list (5 条)
v = run_js(
    f"""
(async () => {{
  const res = await fetch('/cgi-bin/appmsgpublish?sub=list&free_publish_type=1&begin=0&count=5&token={TOKEN}&lang=zh_CN&f=json&ajax=1', {{credentials:'include'}});
  return await res.text();
}})()"""
)
with open(os.path.join(OUT, "publish_list.json"), "w") as f:
    f.write(v)
print("publish_list.json bytes:", len(v))

time.sleep(2)

# fixture 2: datacubequery tmpl=17 近 7 天文章级
today = time.strftime("%Y%m%d")
week_ago = time.strftime("%Y%m%d", time.localtime(time.time() - 7 * 86400))
v2 = run_js(
    f"""
(async () => {{
  const body = new URLSearchParams({{action:'query', busi:'3', tmpl:'17', args: JSON.stringify({{begin_date:'{week_ago}', end_date:'{today}', offset:0, size:10}}), fingerprint:'{FP}', token:'{TOKEN}', lang:'zh_CN', f:'json', ajax:'1'}});
  const res = await fetch('/misc/datacubequery', {{method:'POST', credentials:'include', headers:{{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}}, body: body.toString()}});
  return await res.text();
}})()"""
)
# fixture 落盘前剥离会话字段（token/fingerprint）
v2c = v2.replace(TOKEN, "TOKEN").replace(FP, "FINGERPRINT") if TOKEN and FP else v2
with open(os.path.join(OUT, "datacube_tmpl17.json"), "w") as f:
    f.write(v2c)
print("datacube_tmpl17.json bytes:", len(v2))
