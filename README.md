# mpdt — 公众号运营数据，抓回本机，一键周报

> WeChat Official Account analytics, fully local — fetch, store, and report your own data via one CLI.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green)]()
[![CI](https://github.com/ttxs69/mpdt/actions/workflows/tests.yml/badge.svg)](https://github.com/ttxs69/mpdt/actions/workflows/tests.yml)
[![Tests](https://img.shields.io/badge/tests-98%20passing-brightgreen)]()
[![Dependencies](https://img.shields.io/badge/dependencies-requests%20%2B%20websocket--client-orange)]()

公众号后台（mp.weixin.qq.com）的运营数据散落在三个页面，官方不提供导出，mpdt 把它们自动抓回本机 SQLite，并生成 Markdown 周报：

- **发表列表**：阅读 / 分享 / 点赞 / 评论（实时累计口径）
- **datacube 文章级**：完读率 / 阅读率 / 送达 pv（发表后 7 天窗口，T+1）
- **单篇渠道页**：逐篇阅读人数、完读率、平均时长、渠道构成（推荐 / 搜一搜 / 公众号消息…，30 天窗口）

## 为什么做这个

- **官方不提供导出**：后台数据只能一屏一屏翻页看，换设备、离职交接、跨年对比都无从下手
- **商业工具 ¥99/月起**：第三方 SaaS 要么按月收费，要么要求交出账号托管，个人号主用不起也不敢用
- **个人号主被忽视**：官方与商业工具都面向机构运营团队，一个人打理的号缺少趁手的本地工具

mpdt 的答案：**数据 100% 留在本机**（SQLite + 本地文件），复用你已登录的 Chrome，不托管、不上传、不收费。

> ⚠️ **口径警告**：三源同名指标口径不同（统计窗口 / 分母各异）——发表列表是实时累计，datacube 是发表后 7 天窗口 T+1，单篇渠道页是 30 天窗口。**跨源比较前先对齐口径**，周报文首附完整口径说明。

## 特性

- **零扫码**：复用本机已登录 Chrome 的登录态（CDP 远程调试），token 从页面 URL 自动刷新
- **全本地**：数据只存本机 SQLite（`~/.mpdt/mpdt.sqlite3`），凭据文件 600 权限、打印永不回显
- **幂等**：同一天重复抓取 upsert 不重复；`log_run` 记录每次运行
- **礼貌限速**：请求间隔 ≥ 1s
- **错误语义明确**：退出码 `0` 成功（含空数据）｜`2` 配置问题｜`3` 凭据/登录态问题｜`4` 上游接口异常（附原始返回片段）｜`130` 用户中断（幂等设计，重跑安全）
- **机器可读输出**：全局 `--json` 参数——stdout 恰好一行 JSON（`{ok, cmd, data, meta}`），适合脚本/管道串联
- **非交互友好**：stdout 非 TTY（管道 / 重定向 / cron）时自动静默过程输出
- **零框架测试**：98 个 unittest，零第三方测试依赖，`python3 tests/test_fetchers.py` 直跑

## 安装

```bash
# 推荐：pip 安装
pip install git+https://github.com/ttxs69/mpdt.git

# 或本地安装
git clone https://github.com/ttxs69/mpdt.git
cd mpdt && pip install .
```

> 未安装到环境时，`python3 -m mpdt` 与 `mpdt` 命令完全等价——从仓库根目录直接运行即可。

依赖：Python 3.10+、`requests`、`websocket-client`（其余全标准库）。

## Quick Start（30 秒）

```bash
# ① 以调试端口启动 Chrome 并登录公众号后台
google-chrome --remote-debugging-port=9222
#    打开 https://mp.weixin.qq.com 扫码登录，保持该标签页存在

# ② 初始化配置（生成 ~/.mpdt/config.json，600 权限）
mpdt init

# ③ 抓数据 → 出周报
mpdt fetch --backfill 7   # 抓最近 7 天（默认仅昨天，T+1 口径）
mpdt detail --recent 7    # 单篇渠道构成（最近 7 天发表的）
mpdt report               # → mpdt-reports/weekly-YYYY-Www.md
```

> 已在跑的 Chrome 需先完全退出再用该参数重启；介意隔离可用独立 profile：
> `--user-data-dir=/tmp/mpdt-chrome --remote-debugging-port=9222`

## 命令一览

| 命令 | 作用 | 常用参数 |
|---|---|---|
| `mpdt init` | 生成配置 | `--db PATH` 自定义数据库位置 |
| `mpdt fetch` | 发表列表 + datacube 文章级快照 | `--backfill N` / `--from/--to YYYY-MM-DD` / `--count N` |
| `mpdt detail` | 单篇渠道页（完读率 + 渠道构成） | `--recent N`，或 `--msgid ID --publish-date D [--item-idx 1]` |
| `mpdt report` | 生成周报 Markdown + HTML | `--week 2026-W42` |
| `mpdt export` | 导出快照 CSV（附口径元数据 `*.metrics.csv`） | `--source SOURCE --out OUT`（`--no-meta` 关闭伴随文件） |
| `mpdt metrics` | 列出全部指标及口径 | 可选位置参数按 规范名/源字段/源名 过滤 |

所有命令支持全局 `--json`（stdout 恰好一行 JSON）。

## 指标口径（metrics registry）

| 规范名 | 源字段 | 所属源 | 窗口口径 | 分母 | 单位 | 跨源可比 |
|---|---|---|---|---|---|---|
| 阅读数 | `read_num` | publish_list | 实时累计（拉取时点全量） | - | 人次 | 否 |
| 分享数 | `share_num` | publish_list | 实时累计（拉取时点全量） | - | 次 | 否 |
| 点赞(在看)数 | `like_num` | publish_list | 实时累计（拉取时点全量） | - | 次 | 否 |
| 旧版点赞数 | `old_like_num` | publish_list | 实时累计（拉取时点全量） | - | 次 | 否 |
| 评论数 | `comment_num` | publish_list | 实时累计（拉取时点全量） | - | 条 | 否 |
| 统计日 | `refdate` | datacube_article | 发表后 7 天窗口，T+1 出数 | - | 日期 | 是 |
| 完读率(7天·阅读人数口径) | `finish_read_rate` | datacube_article | 发表后 7 天窗口，T+1 出数 | 7 天窗口内阅读人数 | 比例(0-1) | 否 |
| 阅读率 | `read_rate` | datacube_article | 发表后 7 天窗口，T+1 出数 | 送达人数(send_succ) | 比例(0-1) | 否 |
| 阅读次数 | `total_pv` | datacube_article | 发表后 7 天窗口，T+1 出数 | - | 次 | 否 |
| 完读次数 | `total_read_finish` | datacube_article | 发表后 7 天窗口，T+1 出数 | - | 次 | 否 |
| 分享次数 | `total_share` | datacube_article | 发表后 7 天窗口，T+1 出数 | - | 次 | 否 |
| 送达人数 | `send_succ` | datacube_article | 发表后 7 天窗口，T+1 出数 | - | 人 | 否 |
| 阅读人数(全渠道去重) | `read_uv` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 完读率(30天·pv口径) | `finished_read_pv_ratio` | article_detail | 发表后 30 天窗口 | 阅读次数(pv) | 比例(0-1) | 否 |
| 分享人数 | `share_uv` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 点赞数 | `like_cnt` | article_detail | 发表后 30 天窗口 | - | 次 | 否 |
| 评论数 | `comment_cnt` | article_detail | 发表后 30 天窗口 | - | 条 | 否 |
| 收藏人数 | `collection_uv` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 阅读后关注人数 | `follow_after_read_uv` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 听读人数 | `listen_uv` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 听读次数 | `listen_pv` | article_detail | 发表后 30 天窗口 | - | 次 | 否 |
| 在看数 | `zaikan_cnt` | article_detail | 发表后 30 天窗口 | - | 次 | 否 |
| 赞赏金额 | `praise_money` | article_detail | 发表后 30 天窗口 | - | 上游金额单位 | 否 |
| 平均阅读时长 | `avg_article_read_time` | article_detail | 发表后 30 天窗口 | - | 秒 | 否 |
| 发表日 | `publish_date` | article_detail | 发表后 30 天窗口 | - | 日期 | 是 |
| 窗口天数 | `window_days` | article_detail | 发表后 30 天窗口 | - | 天 | 是 |
| 全渠道阅读人数(汇总) | `total_read_user` | article_detail | 发表后 30 天窗口 | - | 人 | 否 |
| 渠道构成 | `channel_breakdown` | article_detail | 发表后 30 天窗口 | 「全部」渠道阅读人数 | JSON | 否 |

易混淆跨源配对（数值不可直接比）：
- `finish_read_rate`(datacube_article) vs `finished_read_pv_ratio`(article_detail)：同为「完读率」：7天窗口·阅读人数分母 vs 30天窗口·pv 分母，数值不可直接比
- `total_pv`(datacube_article) vs `read_uv`(article_detail)：同为「阅读量级」：次数口径 vs 去重人数口径
- `read_num`(publish_list) vs `read_uv`(article_detail)：实时累计 vs 30 天窗口去重人数

> 本节由 `mpdt/metrics.py` 注册表生成（`python3 -c "from mpdt.metrics import readme_section; print(readme_section())"`），改口径请改注册表后同步本节，或运行 `mpdt metrics list` 查看最新口径。

## FAQ

- **fetch 提示「该日期范围无数据」**：不是错误（退出码 0）。datacube 为 T+1 口径，当天数据明天可得。
- **退出码 3（AuthError）**：CDP 9222 上没有已登录的 mp.weixin 标签页——回 Quick Start ① 检查 Chrome 参数与登录态。
- **退出码 4（UpstreamError）**：微信接口返回了未识别结构——错误信息附原始返回片段，提 issue 时请一并贴出。
- **换机器 / 重置**：删除 `~/.mpdt/` 后重跑 `mpdt init`。数据库与配置都在该目录（除非 `--db` 另指定）。
- **会碰我的 Chrome 吗**：只通过 CDP 读取已登录标签页的 URL 与发起后台 API 请求，不注入脚本、不改动页面。

## 文档

- [需求背景](docs/requirements.md)
- [产品需求（PRD）](docs/product-requirements.md)
- [技术设计](docs/technical-design.md)
- [冷启动记录](docs/coldstart.md)
- 答疑：[提 issue](https://github.com/ttxs69/mpdt/issues)

## 边界与合规

- 只读取**你自己**公众号后台的数据；不提供任何刷量 / 代抓他人数据能力
- 自动化访问后台属微信 ToS 灰色地带，本项目仅限个人备份分析用途，风险自担

## License

[MIT](LICENSE) © ttxs69
