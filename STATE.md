# mp-data-tool · STATE.md

产品：公众号运营数据自动化工具（exploration.md C1，用户 2026-10-17 00:10 批准进入验证）
流程框架：product-lifecycle-navigator（七阶段带质量门）

## 当前阶段
- [x] 阶段 0 需求分析 — **2026-10-16 16:50 CST 完成，过质量门**（8 条证据/画像 3+反画像/实验 V1 预写判定）
- [x] 阶段 1 PRD — **2026-10-17 01:1x CST 完成，过质量门**（02-prd.md：八节/P0P1P2/Given-When-Then×8/OutOfScope×6/退出码语义 0-4；形态落定=本地 Python CLI，开源核心+付费增值候选；首次写作 16:53 因 API 超时中断，本轮补跑成功——骨架先落盘策略生效）
- [x] 阶段 2+3 技术方案+评审（合并文档）— **2026-10-17 01:2x CST 完成，过质量门**（03-technical-design.md：架构=单包 CLI stdlib+requests 唯一依赖、被否方案×4、数据流含回填边界、外部成本全 0、风险 R1-R5 带缓解、MVP=仅 P0、全本地无不可回滚路径；三角色评审 BLOCKER=0、WARN×4、NOTE×3）
- [x] 阶段 4 实现 · 第一轮（2026-10-17 03:4x）：mpdt 包骨架落地（cli/config/session/store/report/fetchers×2）+ 真实 fixture 2 份 + 单测 8/8 + 端到端真跑（fetch backfill 7 → 10+6 行入库；AC2 幂等/AC6 数值一致/AC7 无凭据泄漏均过）。
- [x] 阶段 4 实现 · 第二轮（2026-10-17 04:0x，R28）：**FR3② 渠道页 fetcher 完成**——端点逆向（`action=detailpage` + `type=int&pageVersion=1`；数据在 HTML 内嵌 `window.wx.cgiData`）、scene→渠道映射取自页面 JS 包 sceneTags、fixture×3 真实录制、单测 15/15、`mpdt detail` 子命令 + 真跑 9 篇入库（0 失败）、幂等复验（25 行不变）、AC7 扫描通过、周报新增「单篇渠道构成」表 + 口径说明校正（T8）。
- [x] 阶段 4 收尾（2026-10-17 04:5x，R29）：**README 三步指引 + 隔离 HOME 新手走查**——走查实测 7 项：init 600 权限 ✅、无 Chrome 报错路径 ✅、空库 report ✅、exit 2 ✅、真实 fetch 20 篇 ✅（本机常驻 Chrome 登录态在新 HOME 下仍可用，0→1 路径实证）、修复走查发现的 bug（CDP 连不上时裸 URLError traceback + exit 1 → 包成 AuthError exit 3 + 修复指引，15/15 测试过 + 真路径无回归复验）、退出码语义写进 `--help`（外部惯例：sysexits/square/exit 均要求文档化）。外部做法采纳 1 条。
- [x] 阶段 6 打磨 · 第一轮（2026-10-17 08:5x，R34）：**`--json` 机器可读输出 + TTY 检测静默 + Ctrl-C exit 130**——CLI 全子命令接入 `_R` 输出路由（过程 say/结果 out/结构化 data 三层分离）；`--json` 时 stdout 恰好一行 `{ok,cmd,data,meta}`，错误同样给 JSON（exit 2 实测）；非 TTY 自动静默过程输出；KeyboardInterrupt → exit 130（T1「超时/中断≠失败」三态语义产品化：0/130/2-4 可判）。单测 15→19（新增 json 单行/error payload/exit 130/quiet 四条），真路径复验：--json report ✅、非 TTY detail 静默过程只留结果行 ✅、--json detail --msgid 缺参 rc=2 JSON ✅、interrupt rc=130 ✅。README 特性段同步。
- [ ] 阶段 6 打磨 · 候选：`export --json`、report HTML 模板化、mpdt GEO 适配首测（R35 菜单）。
- [ ] 阶段 4 前置：**V1 验证实验执行中**——公众号版草稿已入箱（2026-10-16 17:5x，appmsgid 100001544，读回校验 7 项全过，待用户终审发布）；掘金/V2EX 主渠道版待用户反馈后写。判定规则见 01-requirements §6（≥20 留资=通过）。
- [ ] **04-coldstart.md 冷启动深挖链**（2026-10-17 02:4x，v5 首跑产出）：前 10 用户=5-8 次 1:1 手工转化（主路径 V1 验证文+留资+搜一搜长尾）；V2EX 降级辅助（T7 密度实证）；Q4 渠道侦察 10-18 后执行；开源动作（L3-2）执行前须用户终审（Q2）。
- [ ] 阶段 5 数据分析
- [ ] 阶段 6 迭代

## 关键决策记录
- 2026-10-16 16:50：阶段 0 结论=需求真实（付费事实+一手故事+自家亲证），V1 验证实验（踩坑技术文+留资）为下一动作；托管形态因费用边界+ToS 灰色默认不做。
- 2026-10-17 01:1x：阶段 1 产品形态落定=**本地 Python CLI**（复用 mp-datacube.py 已验证通道；被否：托管 SaaS=费用+ToS+隐私卖点自相矛盾、浏览器插件=正面撞壹伴+形态重、Web GUI=MVP 不需要）。商业形态候选=开源免费核心+付费增值（模板包/进阶功能），一次性买断优先于订阅（外部参照：工具类买断心理门槛低）；正式命名与定价 TBD（阶段 3 商人视角终审）。MVP=P0 六功能（init/fetch/snapshot/report/错误语义/token 刷新），两周内最小闭环。

## 开放风险
- 微信 ToS 灰色（自动化访问后台）：验证期只做教程+留资，零风险路径；正式形态需用户终审。
- mp-datacube.py token/fingerprint 会过期：已定为 P0 功能 FR2（自动刷新，URL 正则方案已验证可行）。
- 上游接口变更风险：PRD FR6 已定错误语义（退出码 4+原始返回片段），阶段 2 需设计降级路径。

## 阶段 2 输入
02-prd.md §5（P0=FR1-FR6）/§6 NFR/§7 AC1-AC7；复用资产=mp-datacube.py、ai-trends sparkline、daily-ledger 草稿通道（FR9 P1）。
