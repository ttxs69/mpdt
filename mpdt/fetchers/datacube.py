"""fetcher: datacube 文章级表（/misc/datacubequery tmpl=17）。

真实结构（fixture tests/fixtures/datacube_tmpl17.json）：
  {base_resp, delay, data: [ {appmsgid, itemidx, refdate, title, finish_read_rate,
   read_rate, total_pv, total_read_finish, total_share, send_succ, ...} ]}
口径：仅统计发表后 7 天内数据；total_pv 只算送达（群发）阅读（README 口径表）。
"""
import json
import time

from ..errors import UpstreamError

QUERY_JS = """
(async () => {
  const body = new URLSearchParams({action:'query', busi:'3', tmpl:'17',
    args: JSON.stringify({begin_date:'%s', end_date:'%s', offset:0, size:%d}),
    fingerprint:'%s', token:'%s', lang:'zh_CN', f:'json', ajax:'1'});
  const res = await fetch('/misc/datacubequery', {method:'POST', credentials:'include',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}, body: body.toString()});
  return await res.text();
})()"""


def parse_tmpl17(raw: str) -> list:
    try:
        d = json.loads(raw)
    except ValueError:
        raise UpstreamError('datacube 非JSON: ' + raw[:200])
    if 'data' not in d or 'base_resp' not in d:
        raise UpstreamError('datacube 结构变更（data/base_resp 缺失）: ' + raw[:200])
    rows = d.get('data') or []
    if rows and 'finish_read_rate' not in rows[0]:
        raise UpstreamError('datacube 行结构变更（finish_read_rate 缺失）: ' + raw[:200])
    return rows


def fetch(session, begin: str, end: str, size=40) -> list:
    """begin/end: YYYYMMDD（Asia/Shanghai 口径，调用方转换）。"""
    raw = session.fetch_text(QUERY_JS % (begin, end, size, session.fingerprint, session.ensure_token()))
    return parse_tmpl17(raw)
