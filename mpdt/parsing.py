"""纯解析层：微信接口的原始文本 → 结构化数据。

这些函数不碰网络、不读文件、不看时钟。拆出来的理由很实在：解析规则本身就是
口径——哪种结构算「上游改版」、哪些行要跳过、字段缺失回退到哪里——验证这些
问题不该需要一台登录了公众号后台的 Chrome。

取数在 `fetchers/*`（壳）：拿到原始文本后立刻交给这里，壳里不再出现 `json.loads`。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
from typing import Any, cast

from .errors import UpstreamError

# ═══════════════════════ 发布列表 ═══════════════════════


def _decode_outer(raw: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(raw))
    except ValueError as e:
        raise UpstreamError("publish_list 外层非 JSON: " + raw[:200]) from e


def _publish_page_entries(outer: dict[str, Any], raw: str) -> tuple[Any, ...]:
    """publish_page 可能是一层字符串再套一层 JSON → (entries, pp)。"""
    pp = outer.get("publish_page")
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except ValueError as e:
            raise UpstreamError("publish_page 二次解码失败: " + str(pp)[:200]) from e
    entries = pp.get("publish_list") if isinstance(pp, dict) else None
    if not isinstance(entries, list):
        raise UpstreamError("publish_list[] 缺失（结构变更）: " + raw[:200])
    return entries, pp


def _entry_publish_info(entry: dict[str, Any]) -> Any:
    """单条的 publish_info；坏行返回 None（不炸整批，journal 里可见）。"""
    info = entry.get("publish_info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except ValueError:
            return None
    return info if isinstance(info, dict) else None


def _publish_timestamp(info: dict[str, Any]) -> Any:
    """群发时刻：优先 publish_info.create_time，贴图类回退 sent_info.time。"""
    ct = (info.get("publish_info") or {}).get("create_time")
    if isinstance(ct, int) and ct > 0:
        return ct
    # 贴图类（type=9 群发图）publish_info 为空对象，群发时间在 sent_info.time
    # （R70 实证：2247485215 sent_info.time=1792145145 = 2026-10-16 18:05:45+08，
    #  与 publish-read.py 实查 create_time 一致；保持 article 侧口径=群发时刻）
    return (info.get("sent_info") or {}).get("time")


def _publish_date(ct: int | None) -> str:
    """epoch 秒 → Asia/Shanghai 的 YYYY-MM-DD；非法值返回空串。"""
    if not (isinstance(ct, int) and ct > 0):
        return ""
    stamp = _dt.datetime.fromtimestamp(ct, _dt.timezone(_dt.timedelta(hours=8)))
    return stamp.date().isoformat()


def _articles_from_info(info: dict[str, Any], ct: Any, pub_date: str) -> list[Any]:
    """一条群发记录 → 若干 article（已删除的子项跳过）。"""
    out = []
    for a in info.get("appmsg_info") or []:
        if a.get("is_deleted"):
            continue
        out.append(
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
    return out


def parse_publish_list(raw: str) -> dict[str, Any]:
    """双重 JSON 解码 + 结构断言（T2）；结构变更 → UpstreamError（退出码 4）。

    create_time 取自 publish_info.create_time（epoch 秒，Asia/Shanghai 口径），
    用于把「发表记录」行与单篇分析页（需 publish_date 参数）对齐。
    """
    outer = _decode_outer(raw)
    ret = (outer.get("base_resp") or {}).get("ret")
    if ret != 0:
        raise UpstreamError(f"publish_list base_resp.ret={ret}: " + raw[:200])
    entries, pp = _publish_page_entries(outer, raw)
    articles = []
    for entry in entries:
        info = _entry_publish_info(entry)
        if info is None:
            continue
        ct = _publish_timestamp(info)
        articles.extend(_articles_from_info(info, ct, _publish_date(ct)))
    return {
        "total_publish": pp.get("total_count"),
        "publish_count": pp.get("publish_count"),
        "articles": articles,
    }


# ═══════════════════════ datacube 文章级表 ═══════════════════════


# 数值字段归一化：上游把所有值序列化为字符串（"'0.1667'"），入库前统一转数值；
# refdate 等日期字段保持字符串。未列出字段原样保留。
_NUM_FIELDS = (
    "finish_read_rate",
    "read_rate",
    "total_pv",
    "total_read_finish",
    "total_share",
    "send_succ",
)


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in _NUM_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            t = v.strip().strip("'")
            # 非数值字符串原样保留，由上游异常路径兜底
            with contextlib.suppress(ValueError):
                out[k] = int(t) if t.isdigit() else float(t)
    return out


def parse_tmpl17(raw: str) -> list[Any]:
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise UpstreamError("datacube 非JSON: " + raw[:200]) from e
    if "data" not in d or "base_resp" not in d:
        raise UpstreamError("datacube 结构变更（data/base_resp 缺失）: " + raw[:200])
    rows = d.get("data") or []
    if rows and "finish_read_rate" not in rows[0]:
        raise UpstreamError("datacube 行结构变更（finish_read_rate 缺失）: " + raw[:200])
    return [_coerce_row(r) for r in rows]


# ═══════════════════════ 单篇渠道页 ═══════════════════════


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
    agg: dict[Any, Any] = {}
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
