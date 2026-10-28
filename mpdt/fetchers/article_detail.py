"""fetcher: 单篇图文分析页（渠道页，FR3②）—— /misc/appmsganalysis?action=detailpage。

真实结构（fixture tests/fixtures/article_detail_*.html，2026-10-17 录制）：
  HTML 内嵌  window.wx.cgiData = { articleData, articleSummaryData, detailData };
  · articleData.article_data_new: finished_read_pv_ratio(完读率)、avg_article_read_time(秒)、
    read_uv、share_uv、like_cnt、comment_cnt、collection_uv、follow_after_read_uv、listen_uv …
  · articleSummaryData.list: 逐日 × 渠道(scene) 的 read_user/share_user（scene=9999 为「全部」，去重口径）

渠道 scene → 标签映射（primary source：页面 JS 包 mass_detail_new.*.js 的 sceneTags，实测录制）：
  9999 全部｜0 公众号消息｜1 聊天会话｜2 朋友圈｜4 公众号主页｜5 其它｜6 推荐｜7 搜一搜（无 scene=3）

口径（页面原文，2026-10-17）：数据口径自 2025-11-01 起；「推荐」含看一看/图文页底部相关推荐/
快讯/朋友爱心赞；「公众号消息」指公众号列表非看一看区域；**同一用户多渠道阅读时各渠道人数
按渠道分别计数，占比之和可 >100%（仅「全部」去重）**；本页仅统计发表后 30 天内数据。
"""

import json
from typing import Any

from ..errors import UpstreamError

DETAIL_JS = """
(async () => {
  const res = await fetch('/misc/appmsganalysis?action=detailpage&msgid=%s_%d&publish_date=%s&type=int&pageVersion=1&token=%s&lang=zh_CN', {credentials:'include'});
  const t = await res.text();
  return res.status + '|' + t;
})()"""

SCENE_LABELS = {
    9999: "全部",
    0: "公众号消息",
    1: "聊天会话",
    2: "朋友圈",
    4: "公众号主页",
    5: "其它",
    6: "推荐",
    7: "搜一搜",
}
ALL_SCENE = 9999

METRIC_KEYS = (
    "read_uv",
    "share_uv",
    "like_cnt",
    "comment_cnt",
    "collection_uv",
    "follow_after_read_uv",
    "listen_uv",
    "listen_pv",
    "zaikan_cnt",
    "praise_money",
    "avg_article_read_time",
    "finished_read_pv_ratio",
)


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


def _split_status(raw: str) -> tuple[Any, ...]:
    """'HTTP状态|HTML' → (status, html)，并在此处拦掉空响应和非 200。"""
    status, _, html = (raw or "").partition("|")
    if not html:
        raise UpstreamError("detailpage 返回为空: " + (raw or "")[:200])
    if status.strip() != "200":
        raise UpstreamError(f"detailpage HTTP {status.strip()}: {html[:200]}")
    return status, html


def _parse_parts(html: str) -> dict[str, Any]:
    """页面里三段内嵌 JSON（articleData / articleSummaryData / detailData）。"""
    parts = {}
    for key in ("articleData", "articleSummaryData", "detailData"):
        seg = _json_after(html, key)
        if seg is None:
            raise UpstreamError(f"detailpage 结构变更：{key} 缺失: {html[:200]}")
        try:
            parts[key] = json.loads(seg)
        except ValueError as e:
            raise UpstreamError(f"detailpage {key} 非 JSON: {seg[:200]}") from e
    return parts


def _channel_rows(rows: list[Any]) -> list[Any]:
    """articleSummaryData.list → 渠道行（跳过没有 scene 的行）。"""
    channels = []
    for r in rows:
        scene = r.get("scene")
        if scene is None:
            continue
        channels.append(
            {
                "scene": scene,
                "scene_label": SCENE_LABELS.get(scene, f"未知({scene})"),
                "ref_date": r.get("ref_date"),
                "read_user": r.get("read_user", 0),
                "share_user": r.get("share_user", 0),
            }
        )
    return channels


def parse_detail(raw: str) -> dict[str, Any]:
    """raw = 'HTTP状态|HTML'。结构断言失败 → UpstreamError（退出码 4）。"""
    _status, html = _split_status(raw)
    if "articleData" not in html:
        raise UpstreamError(
            "detailpage 结构变更：未找到 articleData（可能参数/版本变化）: " + html[:200]
        )
    parts = _parse_parts(html)

    art = parts["articleData"]
    msgid = str(art.get("msgid") or "")
    if "_" not in msgid:
        raise UpstreamError("articleData.msgid 缺失/异常: " + str(art.get("msgid"))[:80])
    appmsgid, _, itemidx = msgid.partition("_")
    new_data = art.get("article_data_new")
    if not isinstance(new_data, dict):
        raise UpstreamError("article_data_new 缺失（旧口径文章？）: " + msgid)

    rows = (parts.get("articleSummaryData") or {}).get("list") or []
    channels = _channel_rows(rows)
    out = {
        "appmsgid": appmsgid,
        "itemidx": int(itemidx or 1),
        "title": art.get("title", ""),
        "publish_date": art.get("publish_date", ""),
        "is_new_data": art.get("is_new_data"),
        "publishtype": art.get("publishtype"),
        "metrics": {k: new_data.get(k) for k in METRIC_KEYS},
        "channels": channels,
        "detail_data": parts.get("detailData") or {},
        "window_days": 30,
    }
    out["channel_breakdown"] = channel_breakdown(channels)
    return out


def channel_breakdown(
    channels: list[Any], days: int | None = None, publish_date: str | None = None
) -> list[Any]:
    """渠道构成：聚合「除 9999 外」各 scene 的 read_user/share_user，占比分母=「全部」合计。

    days 给定则只统计发表后 days 天（含发表日，publish_date 需为 YYYY-MM-DD 或 YYYY/MM/DD）。
    口径提示：同一用户多渠道会重复计数 → 占比之和可 >100%（与页面「阅读渠道构成」一致）。
    """

    def _norm(d: Any) -> Any:
        return (d or "").replace("/", "-")

    lo, hi = None, None
    if days and publish_date:
        import datetime as dt

        d0 = dt.date.fromisoformat(_norm(publish_date))
        lo, hi = d0.isoformat(), (d0 + dt.timedelta(days=days - 1)).isoformat()

    total = 0
    agg = {}
    for r in channels:
        rd = _norm(r.get("ref_date"))
        if lo and not (lo <= rd <= hi):
            continue
        if r["scene"] == ALL_SCENE:
            total += r.get("read_user", 0)
            continue
        a = agg.setdefault(
            r["scene"],
            {
                "scene": r["scene"],
                "scene_label": SCENE_LABELS.get(r["scene"], str(r["scene"])),
                "read_user": 0,
                "share_user": 0,
            },
        )
        a["read_user"] += r.get("read_user", 0)
        a["share_user"] += r.get("share_user", 0)
    out = []
    for a in sorted(agg.values(), key=lambda x: -x["read_user"]):
        a["read_pct"] = round(100.0 * a["read_user"] / total, 1) if total else None
        out.append(a)
    return out


def fetch(session: Any, appmsgid: Any, itemidx: Any, publish_date: Any) -> dict[str, Any]:
    """publish_date: YYYY-MM-DD（页面 URL 参数口径）。"""
    raw = session.fetch_text(
        DETAIL_JS % (appmsgid, int(itemidx), publish_date, session.ensure_token())
    )
    return parse_detail(raw)
