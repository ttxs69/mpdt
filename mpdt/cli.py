#!/usr/bin/env python3
"""mpdt CLI（FR1/FR3/FR4/FR5/FR6）。子命令：init / fetch / detail / report / export / metrics / doctor / status。
退出码：0 成功（含空数据）｜2 配置｜3 凭据｜4 上游异常｜130 用户中断。"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import stat as stat_mod
import sys
import urllib.error
import urllib.request

from . import __version__, config, store, report, metrics as metrics_mod
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

    def progress(self, i, n, msg):
        """进度行：[第 N/总] msg（TTY 人读；--json/管道静默）。"""
        self.say(f'  [{i}/{n}] {msg}')

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

# 快照数据源（与 store.SCHEMA 注释、metrics.SRC_* 一致）：export --source 合法值
SOURCES = ['publish_list', 'datacube_article', 'article_detail']


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
    r.say(f'拉取发表列表（count={args.count}）…')
    pl = publish_list.fetch(s, count=args.count)
    for i, a in enumerate(pl['articles'], 1):
        r.progress(i, len(pl['articles']), f'入库 {a["title"][:24]}')
        # R101：create_time/publish_date/send_hour 元数据随行入库（C20 publish_window
        # 消费链的 L1 缺口——解析层早有、入库曾丢弃）。send_hour=群发时刻北京时间
        # HH:MM；上游缺 create_time 时三键均存 null，不虚构。
        ct = a.get('create_time')
        send_hour = None
        if isinstance(ct, int) and ct > 0:
            send_hour = dt.datetime.fromtimestamp(
                ct, dt.timezone(dt.timedelta(hours=8))).strftime('%H:%M')
        store.upsert(con, 'publish_list', stat_date, a['appmsgid'], a['itemidx'],
                     a['title'], {**{k: a.get(k) for k in
                                     ('read_num', 'share_num', 'like_num',
                                      'old_like_num', 'comment_num')},
                                  'create_time': ct if isinstance(ct, int) and ct > 0 else None,
                                  'publish_date': a.get('publish_date') or None,
                                  'send_hour': send_hour})
    n_total += len(pl['articles'])
    store.log_run(con, 'fetch:publish_list', True, f"{len(pl['articles'])} articles")
    r.say(f'拉取 datacube（{begin} ~ {end}，T+1 口径）…')
    dc = datacube.fetch(s, begin.strftime('%Y%m%d'), end.strftime('%Y%m%d'))
    for i, r2 in enumerate(dc, 1):
        r.progress(i, len(dc), f'入库 {r2.get("title", "")[:24]}')
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
    n_targets = len(targets)
    for t_i, (mid, idx, pub) in enumerate(targets, 1):
        r.progress(t_i, n_targets, f'{mid}_{idx}（≥1s/篇限速）')
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
    return r.result(f"周报已生成：{out['md']}（数据行 {out['rows']}，跨源对照 {out['cross_rows']} 行）",
                    {'md': out['md'], 'html': out['html'], 'rows': out['rows'],
                     'cross_rows': out['cross_rows'],
                     'concepts_compared': out.get('concepts_compared', []),
                     'concepts_empty': out.get('concepts_empty', []),
                     'week': week})


# ---- doctor / status：一站式体检与状态查询（UX 打磨，全部只读） ----

def _db_readonly_stats(db_path):
    """以只读方式打开 db，返回 {exists, rows_by_source, latest_snapshot, db_bytes, error}。"""
    out = {'exists': os.path.exists(db_path), 'rows_by_source': {},
           'latest_snapshot': None, 'db_bytes': None, 'error': None}
    if not out['exists']:
        return out
    out['db_bytes'] = os.path.getsize(db_path)
    try:
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=2)
        try:
            for src in ('publish_list', 'datacube_article', 'article_detail'):
                out['rows_by_source'][src] = con.execute(
                    'SELECT COUNT(*) FROM snapshots WHERE source=?', (src,)).fetchone()[0]
            row = con.execute('SELECT MAX(stat_date) FROM snapshots').fetchone()
            out['latest_snapshot'] = row[0] if row else None
        finally:
            con.close()
    except sqlite3.Error as e:
        out['error'] = str(e)
    return out


def cmd_doctor(args):
    """一键体检（只读）：配置 / 权限 / Chrome CDP / db / 快照新鲜度逐项检查。

    人读输出 ✓/✗/⚠ 清单；--json 输出同结构 data；始终 exit 0（诊断结果不是命令成败）。
    """
    r = RES[0]
    items = []

    def add(name, status, detail, data=None):
        items.append({'name': name, 'status': status, 'detail': detail,
                      **(data or {})})

    # ① 配置文件存在
    cfg_exists = os.path.exists(config.CONFIG_PATH)
    add('配置文件', 'ok' if cfg_exists else 'fail',
        config.CONFIG_PATH if cfg_exists else f'未找到 {config.CONFIG_PATH} → 运行 mpdt init')

    # ② 配置可解析（存在时）
    cfg = None
    if cfg_exists:
        try:
            cfg = config.load()
            add('配置解析', 'ok', 'JSON 可读，默认项已合并')
        except Exception as e:
            add('配置解析', 'fail', f'损坏（{e}）→ 删除后重跑 mpdt init')

    # ③ 配置权限 600
    if cfg_exists:
        mode = stat_mod.S_IMODE(os.stat(config.CONFIG_PATH).st_mode)
        perms = oct(mode)[2:]
        if mode == 0o600:
            add('配置权限', 'ok', '600（仅本用户可读写）')
        else:
            add('配置权限', 'warn', f'{perms}（期望 600）→ chmod 600 ' + config.CONFIG_PATH)

    # ④ Chrome CDP 可达性（5s 超时，只探不发命令）
    cdp_url = (cfg or {}).get('chrome_cdp', config.DEFAULTS['chrome_cdp'])
    cdp_detail, mp_tab = f'{cdp_url} 不可达（先以 --remote-debugging-port=9222 启动 Chrome）', False
    try:
        tabs = json.load(urllib.request.urlopen(cdp_url + '/json', timeout=5))
        mp_tab = any(t.get('type') == 'page' and 'mp.weixin' in t.get('url', '')
                     for t in tabs if isinstance(t, dict))
        if mp_tab:
            cdp_detail = f'{cdp_url} 可达，且存在已登录态的 mp.weixin 标签页'
        else:
            cdp_detail = f'{cdp_url} 可达，但无 mp.weixin 标签页 → 在该 Chrome 里打开并登录 mp.weixin.qq.com'
        add('Chrome CDP', 'ok' if mp_tab else 'warn', cdp_detail,
            {'cdp_url': cdp_url, 'mp_tab': mp_tab})
    except Exception as e:
        add('Chrome CDP', 'fail', cdp_detail, {'cdp_url': cdp_url, 'error': str(e)[:120]})

    # ⑤ token（cfg 里有则只报存在性，绝不回显）
    has_token = bool((cfg or {}).get('token'))
    add('token', 'ok' if has_token else 'warn',
        '已缓存（隐藏显示，过期时 fetch 会经 CDP 自动刷新）' if has_token
        else '未缓存（首次 fetch 会经 Chrome 自动获取，不影响使用）')

    # ⑥ db 文件 / 各源行数 / 最近快照日
    db_path = (cfg or {}).get('db_path', config.DEFAULTS['db_path'])
    st = _db_readonly_stats(db_path)
    if not st['exists']:
        add('数据库文件', 'warn', f'{db_path} 尚不存在（首次 fetch 时自动创建）')
    elif st['error']:
        add('数据库文件', 'warn', f'{db_path} 存在但只读打开失败：{st["error"]}（可能正在被写入）')
    else:
        add('数据库文件', 'ok', f'{db_path}（{st["db_bytes"]} 字节）',
            {'rows_by_source': st['rows_by_source']})
        total_rows = sum(st['rows_by_source'].values())
        if total_rows == 0:
            add('快照数据', 'warn', '0 行（库为空 → 运行 mpdt fetch 建立首份快照）')
        else:
            latest = st['latest_snapshot']
            days_ago = None
            if latest:
                try:
                    days_ago = (today_shanghai() - dt.date.fromisoformat(latest)).days
                except ValueError:
                    pass
            add('快照数据', 'ok' if days_ago is not None and days_ago <= 7 else 'warn',
                f'共 {total_rows} 行，最近快照日 {latest}'
                + (f'（{days_ago} 天前）' if days_ago is not None else '')
                + ('' if days_ago is not None and days_ago <= 7 else ' → 建议运行 mpdt fetch 更新'),
                {'latest_snapshot': latest, 'days_ago': days_ago})

    # ---- 人读输出（--json 走 r.result 通道）----
    if not JSON_FLAG[0]:
        icon = {'ok': '✓', 'warn': '⚠', 'fail': '✗'}
        print('mpdt 体检（只读，不改任何状态）：')
        for it in items:
            print(f'  {icon[it["status"]]} {it["name"]}：{it["detail"]}')
    n_fail = sum(1 for i in items if i['status'] == 'fail')
    n_warn = sum(1 for i in items if i['status'] == 'warn')
    return r.result(f'体检完成：{len(items)} 项，✓ {len(items)-n_fail-n_warn} / ⚠ {n_warn} / ✗ {n_fail}',
                    {'items': items, 'checks': len(items),
                     'ok_count': len(items) - n_fail - n_warn,
                     'warn_count': n_warn, 'fail_count': n_fail},
                    all_ok=n_fail == 0)


def cmd_status(args):
    """本地库状态一览（只读）：db 位置 / 各源行数 / 最新快照日 / 库体积。"""
    r = RES[0]
    db_path = config.DEFAULTS['db_path']
    cfg_exists = os.path.exists(config.CONFIG_PATH)
    if cfg_exists:
        try:
            db_path = config.load()['db_path']
        except Exception:
            db_path = config.DEFAULTS['db_path']   # 解析失败→回退默认路径并照常汇报
    st = _db_readonly_stats(db_path)
    db_bytes = st['db_bytes'] or 0
    size_h = f'{db_bytes/1024:.1f} KB' if db_bytes < 1024 * 1024 else f'{db_bytes/1048576:.2f} MB'
    data = {'db_path': db_path, 'db_exists': st['exists'], 'db_bytes': db_bytes,
            'db_size_human': size_h, 'rows_by_source': st['rows_by_source'],
            'latest_snapshot': st['latest_snapshot']}
    if not JSON_FLAG[0]:
        print(f'数据库：{db_path}' + ('' if st['exists'] else '（尚不存在，首次 fetch 时创建）'))
        if st['exists']:
            for src, n in st['rows_by_source'].items():
                print(f'  {src:<18} {n} 行')
            print(f'  最新快照日：{st["latest_snapshot"] or "（空库）"}')
            print(f'  库体积：{size_h}（{db_bytes} 字节）')
    return r.result(f'库状态：{db_path}（{sum(st["rows_by_source"].values())} 行，{size_h}）', data)


def cmd_metrics(args):
    """指标口径查询：按概念分组的人读视图（口径唯一事实源=metrics.py CONCEPTS）。

    输出顺序：① 概念 + series；② 元数据字段；③ 旧版字段；④ 自动配对提醒。
    """
    r = RES[0]
    concepts = metrics_mod.to_concepts_view(args.query)
    if not JSON_FLAG[0]:
        # ① 指标概念 + series
        for c in concepts:
            print('## %s（%s）' % (c['display'], c['concept']))
            if c.get('note'):
                print('  > ' + c['note'])
            for s in c['series']:
                print('  - `%s` · %s · 字段 `%s` · 窗口 %s (%d天) · 分母「%s」' % (
                    s['key'], s['source'], s['field'], s['window'], s['window_days'],
                    s['denominator']))
                if s.get('note'):
                    print('      注：' + s['note'])
            print()
        # ② 元数据字段（非指标）— 仅当 query 不限 concept / 匹配元数据 key 时显示
        if not args.query or any(args.query.lower() in k.lower() or args.query.lower() in m['display']
                                  for k, m in metrics_mod.META_FIELDS.items()):
            print('── 元数据字段（非指标：快照日 / 派生列 / 结构列） ──')
            for mname, m in metrics_mod.META_FIELDS.items():
                if args.query and args.query.lower() not in (mname + m['display']).lower():
                    continue
                for skey, s in m['series'].items():
                    print('  - `%s`（%s / %s）：源 %s，字段 `%s`，%s，%d天窗口' % (
                        mname, m['display'], m['kind'], s['source'], s['field'],
                        s['window'], s['window_days']))
            print()
        # ③ 旧版字段
        if metrics_mod.DEPRECATED and (not args.query or
                                       any(args.query.lower() in (dn + d.get('display', '')).lower()
                                           for dn, d in metrics_mod.DEPRECATED.items())):
            print('── 旧版字段（保留登记；不参与对照 / 不出现在 metrics 视图） ──')
            for dname, d in metrics_mod.DEPRECATED.items():
                if args.query and args.query.lower() not in (dname + d.get('display', '')).lower():
                    continue
                print('  - `%s`（%s）→ 新字段 `%s`；保留供 CSV 元数据识别上游历史返回。' % (
                    dname, d.get('display', ''), d.get('replaced_by', '?')))
            print()
        # ④ 自动配对提醒
        pairs = metrics_mod.auto_pairs()
        if pairs:
            print('── 跨 series 易混淆配对（自动推导：同概念多 series） ──')
            for cname, a, b, why in pairs:
                display = metrics_mod.CONCEPTS[cname]['display']
                if args.query and args.query.lower() not in (cname + display + a + b).lower():
                    continue
                print('⚠ %s：`%s` vs `%s` — %s' % (cname, a, b, why))
    return r.result('共 %d 个概念，%d 个 series；%d 个元数据字段；%d 个旧版字段' % (
        len(concepts), sum(len(c['series']) for c in concepts),
        len(metrics_mod.META_FIELDS), len(metrics_mod.DEPRECATED)),
        {'concepts': concepts, 'auto_pairs': [
            {'concept': c, 'series_a': a, 'series_b': b, 'why': why}
            for c, a, b, why in metrics_mod.auto_pairs()]},
        total_concepts=len(concepts),
        total_series=sum(len(c['series']) for c in concepts),
        total_meta=len(metrics_mod.META_FIELDS),
        total_deprecated=len(metrics_mod.DEPRECATED),
        query=args.query or '')


def cmd_export(args):
    r = RES[0]
    cfg = config.load()
    con = store.connect(cfg['db_path'])
    if args.format == 'json':
        n = store.export_json(con, args.source, args.out, with_meta=not args.no_meta)
        meta = {'out': args.out, 'rows': n, 'source': args.source, 'format': 'json'}
        msg = f'JSON 导出：{args.out}（{n} 行，source={args.source}）'
        if not args.no_meta:
            msg += '（口径元数据：内嵌 metrics_meta）'
            meta['metrics_meta'] = 'embedded'
    else:
        n = store.export_csv(con, args.source, args.out, with_meta=not args.no_meta)
        meta = {'out': args.out, 'rows': n, 'source': args.source, 'format': 'csv'}
        msg = f'CSV 导出：{args.out}（{n} 行，source={args.source}）'
        if not args.no_meta:
            msg += f'（口径元数据：{args.out}.metrics.csv）'
            meta['metrics_meta'] = args.out + '.metrics.csv'
    return r.result(msg, meta)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog='mpdt',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='公众号运营数据自动化工具（本地）：复用本机已登录 Chrome 的登录态，\n'
                    '把发表列表 / datacube 文章分析 / 单篇渠道页快照进本地 SQLite，\n'
                    '再生成周报与导出。数据只存本机。',
        epilog='三步快速上手：\n'
               '  1. mpdt init                  生成本机配置 ~/.mpdt/config.json\n'
               '  2. mpdt fetch                 快照数据进本地 SQLite（需 Chrome 已启动并登录 mp.weixin.qq.com）\n'
               '  3. mpdt report                生成本周周报（Markdown + HTML）\n'
               '\n'
               '常用补充命令：\n'
               '  mpdt doctor                   一键体检（只读：配置/Chrome/数据库逐项检查）\n'
               '  mpdt status                   查看库位置 / 各源行数 / 最新快照日 / 库体积\n'
               '  mpdt metrics [关键字]         查指标口径（"这个字段是什么口径"）\n'
               '\n'
               '退出码：\n'
               '  0    成功（含空数据——当天数据 T+1 后才可得，空结果不是错误）\n'
               '  2    配置问题（未 init / 配置损坏）→ 下一步：mpdt init\n'
               '  3    凭据问题（token 刷新失败）→ 下一步：Chrome 里登录 mp.weixin.qq.com 后重试\n'
               '  4    上游接口异常（微信端点改版）→ 反馈原始返回片段给开发者\n'
               '  130  用户中断 Ctrl-C（已入库部分安全，幂等重跑）')
    p.add_argument('--json', action='store_true',
                   help='机器可读 JSON 输出（stdout 仅一行 JSON；过程输出与提示全静默）')
    p.add_argument('--version', action='version', version='%(prog)s ' + __version__,
                   help='显示版本号并退出')

    sub = p.add_subparsers(dest='cmd', required=True,
                           metavar='<子命令>',
                           description='子命令（详细用法：mpdt <子命令> --help）：')

    # ---- init ----
    pi = sub.add_parser(
        'init',
        help='初始化本机配置（~/.mpdt/config.json，权限 600）',
        description='初始化本机配置：生成 ~/.mpdt/config.json（权限 600，凭据只存本机）。\n'
                    '幂等——已存在时不覆盖，仅提示。这是首次使用的第一步。')
    pi.add_argument('--db', default=None, metavar='PATH',
                    help='数据库文件路径（默认 ~/.mpdt/mpdt.sqlite3，父目录自动创建）')
    pi.set_defaults(fn=cmd_init)

    # ---- fetch ----
    pf = sub.add_parser(
        'fetch', help='快照发表列表 + datacube 文章数据进本地库',
        description='快照今日数据进本地 SQLite：①发表列表（实时累计指标）；\n'
                    '②datacube 文章级分析（发表后 7 天窗口，T+1 出数——当天数据要明天才有）。\n'
                    '幂等：同一天重跑覆盖同键行。需要本机 Chrome 以调试端口启动且已登录。\n'
                    '新用户前置：mpdt init；拿不准环境就先跑 mpdt doctor。')
    pf.add_argument('--from', dest='from_date', metavar='YYYY-MM-DD',
                    help='datacube 起始日（默认=回填窗口起点；与 --backfill 二选一）')
    pf.add_argument('--to', dest='to_date', metavar='YYYY-MM-DD',
                    help='datacube 截止日（默认=昨天，T+1：当天数据未出）')
    pf.add_argument('--backfill', type=int, default=1, metavar='N',
                    help='回填最近 N 天（默认 1=仅昨天；给 --from 时忽略）')
    pf.add_argument('--count', type=int, default=20, metavar='N',
                    help='发表列表拉取条数（默认 20）')
    pf.set_defaults(fn=cmd_fetch)

    # ---- detail ----
    pd = sub.add_parser(
        'detail', help='抓单篇渠道页（完读率 + 渠道来源构成，30 天窗口）',
        description='抓单篇渠道分析页入库（article_detail 源）：阅读人数/完读率/时长 +\n'
                    '逐渠道人数构成（推荐/搜一搜/公众号消息…），发表后 30 天窗口口径。\n'
                    '两种目标：指定单篇（--msgid + --publish-date），或按发表记录批量（--recent N）。')
    pd.add_argument('--msgid', metavar='ID',
                    help='单个 appmsgid（必须同时给 --publish-date）')
    pd.add_argument('--item-idx', type=int, default=1, metavar='N',
                    help='多图文中的第 N 篇（默认 1；与 --msgid 配对）')
    pd.add_argument('--publish-date', metavar='YYYY-MM-DD',
                    help='该篇发表日期（与 --msgid 配对，渠道页接口必填）')
    pd.add_argument('--recent', type=int, default=0, metavar='N',
                    help='抓最近 N 天发表的文章（0=不用此模式；走发表记录自动定位）')
    pd.add_argument('--count', type=int, default=20, metavar='N',
                    help='发表记录拉取条数（--recent 模式下定位目标用，默认 20）')
    pd.set_defaults(fn=cmd_detail)

    # ---- report ----
    pr = sub.add_parser(
        'report', help='生成周报（Markdown + HTML，含跨源口径对照）',
        description='生成本周（或指定周）数据周报：完读率 Top10 + 跨 series 宽表对照 +\n'
                    '单篇渠道构成。文首自动带口径说明；HTML 带目录导航锚点。\n'
                    '前置：先跑过 mpdt fetch（空周也能出报告，只是标注无数据）。')
    pr.add_argument('--week', metavar='ISO周',
                    help='ISO 周号，如 2026-W42（默认=本周）')
    pr.add_argument('--out', metavar='DIR',
                    help='输出目录（默认 ./mpdt-reports）')
    pr.set_defaults(fn=cmd_report)

    # ---- export ----
    pe = sub.add_parser(
        'export', help='导出快照为 CSV / JSON（附口径元数据）',
        description='把某个数据源的快照导出为 CSV 或 JSON。默认附带口径元数据\n'
                    '（CSV：*.metrics.csv 伴随文件；JSON：内嵌 metrics_meta），\n'
                    '防止混源导出后分不清列口径。空源导出 0 行也算成功。')
    pe.add_argument('--source', default='datacube_article', metavar='SRC',
                    choices=SOURCES,
                    help='数据源：{%(choices)s}（默认 %(default)s；先跑 mpdt status 看各源行数）')
    pe.add_argument('--out', default='mpdt-export.csv', metavar='PATH',
                    help='输出文件路径（默认 %(default)s）')
    pe.add_argument('--format', choices=['csv', 'json'], default='csv',
                    help='导出格式（默认 %(default)s；json=结构化载荷，口径元数据内嵌）')
    pe.add_argument('--no-meta', action='store_true',
                    help='不写口径元数据（CSV 伴随文件 / JSON 内嵌块）')
    pe.set_defaults(fn=cmd_export)

    # ---- metrics ----
    pm = sub.add_parser(
        'metrics', help='列出全部指标及口径（查"这个字段是什么口径"）',
        description='指标口径查询：按概念分组列出全部 series（源/字段/窗口/分母），\n'
                    '含元数据字段、旧版字段与易混淆配对。口径唯一事实源=metrics.py。')
    pm.add_argument('query', nargs='?', default=None, metavar='关键字',
                    help='按 规范名/源字段/源名 子串过滤（如 完读率 / read_num）')
    pm.set_defaults(fn=cmd_metrics)

    # ---- doctor ----
    po = sub.add_parser(
        'doctor', help='一键体检：配置/Chrome/数据库逐项检查（只读）',
        description='一键体检（全部只读，不改任何状态）：配置文件存在与可解析、权限 600、\n'
                    'Chrome CDP 可达性、token 缓存状态、数据库文件与各源行数、最近快照新鲜度。\n'
                    '人读输出 ✓/✗/⚠ 清单（✗/⚠ 均附「下一步怎么办」）；--json 输出同结构 data。\n'
                    '诊断本身永远 exit 0——体检结果不是命令成败。')
    po.set_defaults(fn=cmd_doctor)

    # ---- status ----
    ps = sub.add_parser(
        'status', help='本地库状态：位置/各源行数/最新快照日/库体积（只读）',
        description='本地库状态一览（只读）：数据库文件在哪、三个源各多少行、\n'
                    '最新快照日、库体积。配置缺失时也能跑（回退默认路径）。')
    ps.set_defaults(fn=cmd_status)

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
