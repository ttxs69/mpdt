"""fetcher: datacube 文章级表（/misc/datacubequery tmpl=17）—— 只负责取数。

解析在 `mpdt.parsing.parse_tmpl17`（纯）。

真实结构（fixture tests/fixtures/datacube_tmpl17.json）：
  {base_resp, delay, data: [ {appmsgid, itemidx, refdate, title, finish_read_rate,
   read_rate, total_pv, total_read_finish, total_share, send_succ, ...} ]}
口径：仅统计发表后 7 天内数据；total_pv 只算送达（群发）阅读（README 口径表）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..parsing import parse_tmpl17 as parse_tmpl17  # 兼容旧导入路径

if TYPE_CHECKING:
    from ..session import SessionLike

QUERY_JS = """
(async () => {
  const body = new URLSearchParams({action:'query', busi:'3', tmpl:'17',
    args: JSON.stringify({begin_date:'%s', end_date:'%s', offset:0, size:%d}),
    fingerprint:'%s', token:'%s', lang:'zh_CN', f:'json', ajax:'1'});
  const res = await fetch('/misc/datacubequery', {method:'POST', credentials:'include',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}, body: body.toString()});
  return await res.text();
})()"""


def fetch(session: SessionLike, begin: str, end: str, size: int = 40) -> list[Any]:
    """begin/end: YYYYMMDD（Asia/Shanghai 口径，调用方转换）。"""
    raw = session.fetch_text(
        QUERY_JS % (begin, end, size, session.fingerprint, session.ensure_token())
    )
    return parse_tmpl17(raw)
