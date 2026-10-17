# mpdt — 公众号运营数据自动化工具（本地 CLI）

把你公众号后台（mp.weixin.qq.com）散落在三个页面的数据，自动抓回本机 SQLite，一键生成周报：

- **publish_list**：发表列表（阅读/分享/点赞/评论，实时累计口径）
- **datacube 文章级**：完读率/阅读率/送达 pv（发表后 7 天窗口，T+1）
- **单篇渠道页**：逐篇的阅读人数、完读率、平均时长、渠道构成（推荐/搜一搜/公众号消息…，30 天窗口）

> ⚠️ 三源同名指标口径不同（窗口/分母各异），跨源比较前先对齐——周报文首有完整口径说明。

## 特性

- **零扫码**：复用你本机已登录 Chrome 的登录态（CDP），token 从页面 URL 自动刷新
- **全本地**：数据只存本机 SQLite（`~/.mpdt/mpdt.sqlite3`），凭据文件 600 权限、打印永不回显
- **幂等**：同一天重复抓取 upsert 不重复；`log_run` 记录每次运行
- **礼貌限速**：请求间隔 ≥1s
- **错误语义明确**：退出码 `0` 成功（含空数据）｜`2` 配置问题｜`3` 凭据/登录态问题｜`4` 上游接口异常（附原始返回片段）｜`130` 用户中断（Ctrl-C；幂等设计，重跑安全）
- **机器可读输出**：全局 `--json` 参数——stdout 恰好一行 JSON（`{ok, cmd, data, meta}`），过程输出全静默，适合脚本/管道串联
- **非交互友好**：stdout 非 TTY（管道/重定向/cron）时自动静默过程输出，只打印最终结果行

## 三步上手

### 第 1 步：以调试端口启动 Chrome 并登录后台

```bash
google-chrome --remote-debugging-port=9222
# 打开 https://mp.weixin.qq.com 并扫码登录，保持该标签页存在
```

> 已在跑的 Chrome 需先完全退出再用该参数重启。不关心隔离配置的话，也可以用独立 profile：
> `--user-data-dir=/tmp/mpdt-chrome --remote-debugging-port=9222`

### 第 2 步：初始化配置

```bash
python3 -m mpdt init
# → 生成 ~/.mpdt/config.json（600 权限）
```

### 第 3 步：抓数据 → 出周报

```bash
python3 -m mpdt fetch --backfill 7   # 抓最近 7 天（默认仅昨天，T+1 口径）
python3 -m mpdt detail --recent 7    # 单篇渠道构成（最近 7 天发表的）
python3 -m mpdt report               # → mpdt-reports/weekly-YYYY-Www.md
```

## 命令一览

| 命令 | 作用 | 常用参数 |
|---|---|---|
| `init` | 生成配置 | `--db PATH` 自定义数据库位置 |
| `fetch` | 发表列表 + datacube 文章级快照 | `--backfill N` / `--from/--to YYYY-MM-DD` / `--count N` |
| `detail` | 单篇渠道页（完读率+渠道构成） | `--recent N`（最近 N 天发表）或 `--msgid ID --publish-date D [--item-idx 1]` |
| `report` | 生成周报 Markdown | — |
| `export` | 导出快照 | `--source SOURCE --out OUT` |

依赖：Python 3.10+，`requests`、`websocket-client`（其余全 stdlib）。

## 常见问题

- **fetch 提示"该日期范围无数据"**：不是错误（退出码 0）。datacube 为 T+1 口径，当天数据明天可得。
- **退出码 3（AuthError）**：CDP 9222 上没有已登录的 mp.weixin 标签页——回第 1 步检查 Chrome 参数与登录态。
- **退出码 4（UpstreamError）**：微信接口返回了未识别结构——错误信息附原始返回片段，提 issue 时请一并贴出。
- **换机器/重置**：删除 `~/.mpdt/` 后重跑 `init`。数据库与配置都在该目录（除非 `--db` 另指定）。

## 边界与合规

- 只读取你自己公众号后台的数据；不提供任何刷量/代抓他人数据能力
- 自动化访问后台属微信 ToS 灰色地带，本项目仅限个人备份分析用途，风险自担
