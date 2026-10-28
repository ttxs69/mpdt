"""fetcher: 发布列表（/cgi-bin/appmsgpublish sub=list）—— 只负责取数。

解析在 `mpdt.parsing.parse_publish_list`（纯，可单独测）。

真实结构（fixture tests/fixtures/publish_list.json，2026-10-17 录制）：
  base_resp.ret==0
  publish_page: str  → JSON → {total_count, publish_count, publish_list: [
      {publish_type, publish_info: str → JSON → {type, appmsg_info: [
        {appmsgid, itemidx, title, read_num, share_num, like_num, old_like_num,
         comment_num, create_time(?), content_url, is_deleted, ...}]}]}]}
双重 JSON：publish_page 与 publish_info 均为字符串，需二次 decode。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..parsing import parse_publish_list as parse_publish_list  # 兼容旧导入路径

if TYPE_CHECKING:
    from ..session import SessionLike

LIST_JS = """
(async () => {
  const res = await fetch('/cgi-bin/appmsgpublish?sub=list&free_publish_type=1&begin=0&count=%d&token=%s&lang=zh_CN&f=json&ajax=1', {credentials:'include'});
  return await res.text();
})()"""


def fetch(session: SessionLike, count: int = 20) -> dict[str, Any]:
    raw = session.fetch_text(LIST_JS % (count, session.ensure_token()))
    out = parse_publish_list(raw)
    assert out["articles"] or out["total_publish"] == 0, (
        "parse ok but zero articles with nonzero total"
    )
    return out
