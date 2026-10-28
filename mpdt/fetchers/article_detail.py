"""fetcher: 单篇图文分析页（渠道页，FR3②）—— 只负责取数。

解析在 `mpdt.parsing`（纯）：parse_detail / channel_breakdown / SCENE_LABELS 等。

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

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..parsing import ALL_SCENE as ALL_SCENE
from ..parsing import channel_breakdown as channel_breakdown
from ..parsing import parse_detail as parse_detail

if TYPE_CHECKING:
    from ..session import SessionLike

DETAIL_JS = """
(async () => {
  const res = await fetch('/misc/appmsganalysis?action=detailpage&msgid=%s_%d&publish_date=%s&type=int&pageVersion=1&token=%s&lang=zh_CN', {credentials:'include'});
  const t = await res.text();
  return res.status + '|' + t;
})()"""


def fetch(session: SessionLike, appmsgid: str, itemidx: int, publish_date: str) -> dict[str, Any]:
    """publish_date: YYYY-MM-DD（页面 URL 参数口径）。"""
    raw = session.fetch_text(
        DETAIL_JS % (appmsgid, int(itemidx), publish_date, session.ensure_token())
    )
    return parse_detail(raw)
