#!/usr/bin/env python3
"""mpdt CLI（FR1/FR3/FR4/FR5/FR6）。子命令：init / fetch / report / export。
退出码：0 成功（含空数据）｜2 配置｜3 凭据｜4 上游异常。"""
import argparse
import datetime as dt
import json
import sys

from . import config, store, report
from .errors import MpdtError, UpstreamError


def today_shanghai() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


# ---- 阶段 6 打磨：--json / 静默 TTY 检测 / Ctrl-C（阶段 4 打磨项）----

class _R:
    """收集单命令的结构化结果：print（人读）+ data（--json 时输出）。"""
    def __init__(self, quiet=False):
        self.quiet = quiet   # 非 TTY（管道/重定向/cron）默认静默过程输出
        self.data = None     # dict → --json 时以 JSON 打印
        self.meta = {}       # 机器可读元信息（行数/路径等，与人读文本分离）

    def say(self, msg):
        """过程输出（进度条性质）。--json 时一律不出（污染管道）；非 TTY 且未强制 → 静默。"""
        if not self.quiet and not JSON_FLAG[0]:
            print(msg)

    def out(self, msg, **meta):
        """结果输出（人读版）。--json 时不出，改为 JSON。meta 同步进 self.meta。"""
        self.meta.update(meta)
        if not JSON_FLAG[0]:
            print(msg)

    def result(self, ok_msg, data, **meta):
        """统一收尾：data 为本命令的结构化结果（dict/list）。"""
        self.data = data
        self.meta.update(meta)
        if not JSON_FLAG[0]:
            print(ok_msg)
        return 0

    def payload(self):
        """--json 时的输出体：{ok, cmd, data, meta}——T1 三态在 main() 里以 exit_code 区分，
        timeout/unknown 不与成功混在同一个 JSON 语义里（写后读回型用户拿 exit code 判定）。"""
        return {'ok': True, 'cmd': CMD_NAME[0], 'data': self.data, 'meta': self.meta}


JSON_FLAG = [False]   # main() 解析后置位；模块级因 argparse 在 fn 内之后执行
CMD_NAME = ['']


def cmd_init(args):
    import os
    existed = os.path.exists(config.CONFIG_PATH)
    r = RES[0]
    if existed:
        r.out(f'配置已存在：{config.CONFIG_PATH}（如需重置请手动删除）')
    cfg = dict(config.DEFAULTS)
    cfg['db_path'] = os.path.expanduser(args.db) if args.db else cfg['db_path']
    config.save(cfg)
    r.out(f'已生成 {config.CONFIG_PATH}（权限 600）。')
    r.say('下一步：确保本机 Chrome 以 --remote-debugging-port=9222 启动且已登录 '
          'mp.weixin.qq.com，然后运行 mpdt fetch。')
    return r.result('init 完成', {'config_path': config.CONFIG_PATH,
                                  'db_path': cfg['db_path'], 'existed': existed})


def cmd_fetch(args):
    r = RES[0]
    cfg = config.load()
    from .session import Session
    from .fetchers import publish_list, datacube
    s = Session(cfg)
    con = store.connect(cfg['db_path'])
    today = today_shanghai()
    end = today - dt.timedelta(days=1)          # T+1：昨天是最新可得日
    begin = today - dt.timedelta(days=args.backfill) if args.backfill else end
    if args.from_date:
        begin = dt.date.fromisoformat(args.from_date)
    if args.to_date:
        end = dt.date.fromisoformat(args.to_date)
    stat_date = today.isoformat()               # 快照行=拉取时点，refdate 才是文章口径日
    n_total = 0
    pl = publish_list.fetch(s, count=args.count)
    for a in pl['articles']:
        store.upsert(con, 'publish_list', stat_date, a['appmsgid'], a['itemidx'],
                     a['title'], {k: a[k] for k in
                                  ('read_num', 'share_num', 'like_num', 'old_like_num', 'comment_num')})
    n_total += len(pl['articles'])
    store.log_run(con, 'fetch:publish_list', True, f"{len(pl['articles'])} articles")
    dc = datacube.fetch(s, begin.strftime('%Y%m%d'), end.strftime('%Y%m%d'))
    for r2 in dc:
        store.upsert(con, 'datacube_article', stat_date, r2.get('appmsgid'), r2.get('itemidx', 1),
                     r2.get('title', ''), {k: r2.get(k) for k in
                     ('refdate', 'finish_read_rate', 'read_rate', 'total_pv',
                      'total_read_finish', 'total_share', 'send_succ')})
    n_total += len(dc)
    store.log_run(con, 'fetch:datacube', True, f"{len(dc)} rows {begin}~{end}")
    con.commit()
    data = {'publish_list': len(pl['articles']), 'datacube_rows': len(dc),
            'begin': begin.isoformat(), 'end': end.isoformat(), 'db_path': cfg['db_path']}
    # T+1 提示（AC4）：空数据不是错误
    if n_total == 0:
        return r.result(f'该日期范围无数据（{begin}~{end}；当天数据 T+1 后可得）。', data)
    r.say('下一步：mpdt report 生成本周周报（Markdown+HTML）。')
    return r.result(
        f'快照完成：publish_list {len(pl["articles"])} 篇 + datacube {len(dc)} 行 → {cfg["db_path"]}',
        data)


RES = [None]   # main() 里按 TTY 情况构造


def cmd_detail(args):
    """单篇渠道页（FR3②）：完读率/时长/互动 + 逐渠道人数构成（含推荐/搜一搜）。"""
    r = RES[0]
    cfg = config.load()
    from .session import Session
    from .fetchers import publish_list as pl_mod, article_detail as ad_mod
    s = Session(cfg)
    con = store.connect(cfg['db_path'])
    today = today_shanghai()
    stat_date = today.isoformat()
    targets = []
    if args.msgid:
        if not args.publish_date:
            if JSON_FLAG[0]:
                print(json.dumps({'ok': False, 'cmd': 'detail', 'error': {
                    'exit_code': 2, 'message': '--msgid 需同时给 --publish-date YYYY-MM-DD'}},
                    ensure_ascii=False))
            else:
                print('[mpdt] 错误（退出码 2）：--msgid 需同时给 --publish-date YYYY-MM-DD',
                      file=sys.stderr)
            return 2
        targets.append((args.msgid, args.item_idx, args.publish_date))
    else:
        days = args.recent or 30
        pl = pl_mod.fetch(s, count=args.count)
        cutoff = (today - dt.timedelta(days=days - 1)).isoformat()
        seen = set()
        for a in pl['articles']:
            if not a.get('publish_date') or a['publish_date'] < cutoff:
                continue
            key = (str(a['appmsgid']), int(a['itemidx']))
            if key in seen:
                continue
            seen.add(key)
            targets.append((key[0], key[1], a['publish_date']))
        r.say(f'最近 {days} 天发表记录命中 {len(targets)} 篇，开始抓单篇渠道页（≥1s/篇）…')
    ok, fail = 0, 0
    ok_items = []
    for mid, idx, pub in targets:
        try:
            d = ad_mod.fetch(s, mid, idx, pub)
        except MpdtError as e:
            fail += 1
            r.say(f'  ! {mid}_{idx} 跳过：{e}')
            print(f'  ! {mid}_{idx} 跳过：{e}', file=sys.stderr)
            store.log_run(con, f'detail:{mid}_{idx}', False, str(e)[:200])
            continue
        metrics = dict(d['metrics'])
        metrics.update({
            'publish_date': d['publish_date'],
            'window_days': d['window_days'],
            'total_read_user': sum(rr['read_user'] for rr in d['channels']
                                   if rr['scene'] == ad_mod.ALL_SCENE),
            'channel_breakdown': d['channel_breakdown'],
        })
        store.upsert(con, 'article_detail', stat_date, d['appmsgid'], d['itemidx'],
                     d['title'], metrics)
        ok += 1
        top = d['channel_breakdown'][0] if d['channel_breakdown'] else None
        rec = next((rr for rr in d['channel_breakdown']
                    if rr['scene_label'] == '推荐'), None)
        ok_items.append({'appmsgid': d['appmsgid'], 'itemidx': d['itemidx'],
                         'title': d['title'], 'publish_date': d['publish_date'],
                         'read_uv': metrics.get('read_uv'),
                         'finished_read_pv_ratio': metrics.get('finished_read_pv_ratio'),
                         'top_channel': (top or {}).get('scene_label'),
                         'top_channel_pct': (top or {}).get('read_pct'),
                         'recommend_pct': (rec or {}).get('read_pct')})
        r.say('  ✓ %s_%d 阅读%s 完读率%s 推荐%s%% 首渠道=%s(%s%%)' % (
            d['appmsgid'], d['itemidx'], metrics.get('read_uv'),
            ('%.1f%%' % (100 * metrics['finished_read_pv_ratio'])
             if isinstance(metrics.get('finished_read_pv_ratio'), (int, float)) else 'NA'),
            (rec or {}).get('read_pct', 'NA'),
            (top or {}).get('scene_label', 'NA'), (top or {}).get('read_pct', 'NA')))
    store.log_run(con, 'fetch:article_detail', ok > 0 or not targets,
                  f'ok={ok} fail={fail} targets={len(targets)}')
    con.commit()
    if targets and ok == 0:
        raise UpstreamError('单篇渠道页全部抓取失败（%d 篇）' % fail)
    return r.result(f'单篇渠道页快照完成：{ok} 篇入库（失败 {fail}）→ {cfg["db_path"]}',
                    {'ok': ok, 'fail': fail, 'targets': len(targets), 'items': ok_items,
                     'db_path': cfg['db_path']})


def cmd_report(args):
    r = RES[0]
    cfg = config.load()
    con = store.connect(cfg['db_path'])
    week = args.week or f'{today_shanghai().isocalendar().year}-W{today_shanghai().isocalendar().week:02d}'
    out = report.generate(con, week, args.out or 'mpdt-reports')
    return r.result(f"周报已生成：{out['md']}（数据行 {out['rows']}）",
                    {'md': out['md'], 'html': out['html'], 'rows': out['rows'], 'week': week})


def cmd_export(args):
    r = RES[0]
    cfg = config.load()
    con = store.connect(cfg['db_path'])
    n = store.export_csv(con, args.source, args.out)
    return r.result(f'CSV 导出：{args.out}（{n} 行，source={args.source}）',
                    {'out': args.out, 'rows': n, 'source': args.source})


def main(argv=None):
    p = argparse.ArgumentParser(prog='mpdt', description='公众号运营数据自动化工具（本地）',
                                epilog='退出码：0 成功（含空数据）｜2 配置问题｜3 凭据/登录态问题｜4 上游接口异常｜130 用户中断(Ctrl-C，进度不保证，已入库部分已 commit)')
    p.add_argument('--json', action='store_true',
                   help='机器可读 JSON 输出（stdout 仅一行 JSON；过程输出与提示全静默）')
    sub = p.add_subparsers(dest='cmd', required=True)
    pi = sub.add_parser('init'); pi.add_argument('--db', default=None); pi.set_defaults(fn=cmd_init)
    pf = sub.add_parser('fetch')
    pf.add_argument('--from', dest='from_date', help='YYYY-MM-DD')
    pf.add_argument('--to', dest='to_date', help='YYYY-MM-DD')
    pf.add_argument('--backfill', type=int, default=1, help='回填最近 N 天（默认1=仅昨天）')
    pf.add_argument('--count', type=int, default=20, help='发布列表条数')
    pf.set_defaults(fn=cmd_fetch)
    pr = sub.add_parser('report'); pr.add_argument('--week', help='ISO 周，如 2026-W42'); pr.add_argument('--out', default=None); pr.set_defaults(fn=cmd_report)
    pd = sub.add_parser('detail', help='单篇渠道页（完读率+渠道构成）')
    pd.add_argument('--msgid', help='单个 appmsgid（需配 --publish-date）')
    pd.add_argument('--item-idx', type=int, default=1)
    pd.add_argument('--publish-date', help='YYYY-MM-DD（与 --msgid 配对）')
    pd.add_argument('--recent', type=int, default=0, help='按发表记录抓最近 N 天发布的文章')
    pd.add_argument('--count', type=int, default=20, help='发表记录拉取条数（--recent 时用）')
    pd.set_defaults(fn=cmd_detail)
    pe = sub.add_parser('export'); pe.add_argument('--source', default='datacube_article'); pe.add_argument('--out', default='mpdt-export.csv'); pe.set_defaults(fn=cmd_export)
    pre = p.parse_known_args(argv)[0] if argv is not None else None
    args = p.parse_args(argv)
    # 全局态：--json 与 TTY 检测（非 TTY=管道/重定向/cron，过程输出默认静默，结果仍打印）
    JSON_FLAG[0] = args.json
    CMD_NAME[0] = args.cmd
    RES[0] = _R(quiet=not sys.stdout.isatty() and not args.json)
    try:
        rc = args.fn(args)
        if args.json and rc == 0:
            print(json.dumps(RES[0].payload(), ensure_ascii=False))
        return rc
    except KeyboardInterrupt:
        # T1 三态：用户中断≠失败≠成功——130 独立退出码；已 upsert 的行随本命令 commit 状态而定，
        # detail/fetch 的粒度级 commit 在循环外，故中断时该批未 commit（幂等重跑安全）。
        print('[mpdt] 用户中断（exit 130）：本次进度未完整 commit，重跑安全（幂等 upsert）。',
              file=sys.stderr)
        return 130
    except MpdtError as e:
        print(f'[mpdt] 错误（退出码 {e.exit_code}）：{e}\n{e.hint}', file=sys.stderr)
        if e.exit_code == 4:
            print(f'[mpdt] 原始返回片段：{str(e)[:300]}', file=sys.stderr)
        return e.exit_code


if __name__ == '__main__':
    sys.exit(main())
