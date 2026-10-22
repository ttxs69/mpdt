"""fetcher: 发布列表（/cgi-bin/appmsgpublish sub=list）。

真实结构（fixture tests/fixtures/publish_list.json，2026-10-17 录制）：
  base_resp.ret==0
  publish_page: str  → JSON → {total_count, publish_count, publish_list: [
      {publish_type, publish_info: str → JSON → {type, appmsg_info: [
        {appmsgid, itemidx, title, read_num, share_num, like_num, old_like_num,
         comment_num, create_time(?), content_url, is_deleted, ...}]}]}]}
双重 JSON：publish_page 与 publish_info 均为字符串，需二次 decode。
"""

import json

from ..errors import UpstreamError

LIST_JS = """
(async () => {
  const res = await fetch('/cgi-bin/appmsgpublish?sub=list&free_publish_type=1&begin=0&count=%d&token=%s&lang=zh_CN&f=json&ajax=1', {credentials:'include'});
  return await res.text();
})()"""


def parse_publish_list(raw: str) -> dict:
    """双重 JSON 解码 + 结构断言（T2）；结构变更 → UpstreamError（退出码 4）。

    create_time 取自 publish_info.create_time（epoch 秒，Asia/Shanghai 口径），
    用于把「发表记录」行与单篇分析页（需 publish_date 参数）对齐。
    """
    import datetime as _dt

    try:
        outer = json.loads(raw)
    except ValueError:
        raise UpstreamError("publish_list 外层非 JSON: " + raw[:200])
    ret = (outer.get("base_resp") or {}).get("ret")
    if ret != 0:
        raise UpstreamError(f"publish_list base_resp.ret={ret}: " + raw[:200])
    pp = outer.get("publish_page")
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except ValueError:
            raise UpstreamError("publish_page 二次解码失败: " + str(pp)[:200])
    entries = pp.get("publish_list") if isinstance(pp, dict) else None
    if not isinstance(entries, list):
        raise UpstreamError("publish_list[] 缺失（结构变更）: " + raw[:200])
    articles = []
    for e in entries:
        info = e.get("publish_info")
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError:
                continue  # 单条坏行不炸整批（journal 里可见）
        if not isinstance(info, dict):
            continue
        ct = (info.get("publish_info") or {}).get("create_time")
        if not (isinstance(ct, int) and ct > 0):
            # 贴图类（type=9 群发图）publish_info 为空对象，群发时间在 sent_info.time
            # （R70 实证：2247485215 sent_info.time=1792145145 = 2026-10-16 18:05:45+08，
            #  与 publish-read.py 实查 create_time 一致；保持 article 侧口径=群发时刻）
            ct = (info.get("sent_info") or {}).get("time")
        pub_date = ""
        if isinstance(ct, int) and ct > 0:
            pub_date = (
                _dt.datetime.fromtimestamp(ct, _dt.timezone(_dt.timedelta(hours=8)))
                .date()
                .isoformat()
            )
        for a in info.get("appmsg_info") or []:
            if a.get("is_deleted"):
                continue
            articles.append(
                {
                    "appmsgid": a.get("appmsgid"),
                    "itemidx": a.get("itemidx", 1),
                    "title": a.get("title", ""),
                    "read_num": a.get("read_num", 0),
                    "share_num": a.get("share_num", 0),
                    "like_num": a.get("like_num", 0),
                    "old_like_num": a.get("old_like_num", 0),
                    "comment_num": a.get("comment_num", 0),
                    "content_url": a.get("content_url", ""),
                    "create_time": ct,
                    "publish_date": pub_date,
                }
            )
    return {
        "total_publish": pp.get("total_count"),
        "publish_count": pp.get("publish_count"),
        "articles": articles,
    }


def fetch(session, count=20) -> dict:
    raw = session.fetch_text(LIST_JS % (count, session.ensure_token()))
    out = parse_publish_list(raw)
    assert out["articles"] or out["total_publish"] == 0, (
        "parse ok but zero articles with nonzero total"
    )
    return out
