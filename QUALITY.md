# QUALITY.md — mpdt 的质量门

一个入口：`./scripts/quality.sh`。本地和 CI 跑的是**同一个文件**，不存在两套标准。

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e . && pip install -r requirements-dev.txt
./scripts/quality.sh
```

## 门里有四件事

| 顺序 | 检查 | 失败意味着 |
|---|---|---|
| 1 | `ruff format --check .` | 代码没格式化（**零存量债**，全绿） |
| 2 | `tools/quality_budget.py` → ruff 错误数 | lint 债**涨了**（棘轮，只许降） |
| 3 | 同脚本 → mypy(strict) 错误数 | 类型债**涨了**（棘轮，只许降） |
| 4 | `pytest --cov` | 测试红，或分支覆盖率 < `fail_under`（pyproject 里写一次：90） |

配置全部在 `pyproject.toml` 的 `[tool.ruff*]` / `[tool.mypy]` / `[tool.pytest.ini_options]` /
`[tool.coverage.*]`，没有 `setup.cfg`、`tox.ini`、`mypy.ini`、`pytest.ini`、`.flake8` 的第二份配置。

## 架构：功能核心 / 命令式外壳

代码分三层，`tests/test_purity.py` 用 AST 钉住边界 —— 写在文档里的架构会退化，
写成断言的不会。

| 层 | 模块 | 只做 | 例 |
|---|---|---|---|
| 整形（纯） | `shape.py` | 数据 → 数据 | 哪些算本周、去重、派生列、导出行、邮件头解码 |
| 解析（纯） | `parsing.py` | 原始文本 → 结构化 | 双重 JSON、结构变更判定、渠道聚合 |
| 渲染（纯） | `render.py` | 数据 → 文本 | Markdown / HTML / 邮件正文 / 人读行 |
| 判定（纯） | `advice.py` `next_step.py` `metrics.py` | 行 → 判定 / 口径表 | 诊断、下一步文案、series 登记 |
| 外壳 | `cli.py` `report.py` `push.py` `store.py` `fetchers/*` | 查库、发信、写盘、打印、退出码 | `collect()` / `_write()` / `_deliver()` / `fetch()` |

两条被强制执行的规则：

1. **纯模块不许碰世界**：不许 import `os`/`sqlite3`/`urllib`/`smtplib`…，不许调用
   `print`/`open`/`.execute`/`.fetchall`/`.urlopen`，也不许反向 import 壳层。
2. **壳函数不超过 30 条语句**（含嵌套）。**每个**函数自动受限，不维护白名单 ——
   白名单只保护它认识的东西，新增一个 200 行的 `cmd_xxx` 会永远不被看见。

守卫生效过两次真实复发：`cmd_detail` 第一次拆完仍有 47 条语句，`send_report` 28 条。

为什么值得：口径问题（"最近 30 天怎么筛"、"取哪条快照"、"完读率分母是谁"）现在
可以喂数据直接验证，不必先起一个数据库和一个假 Chrome。

## 为什么 lint / 类型用「棘轮」而不是「必须先清零」

首次接入（2026-10-22）时：

| 项 | 首日 | 现在 | 说明 |
|---|---|---|---|
| ruff | 2423 条 | **0** | 其中 2077 条是 RUF001/002/003（中文全角标点，纯噪音，已按理由豁免）；其余逐批清完 |
| mypy (strict) | **836** 条 | **0** | 依次降到 443 → 164 → 97 → **0**（见下节） |
| 分支覆盖率 | 84.08% | **97.57%** | 443 个测试（门限 96%） |
| 格式 | 37 文件待改 | **0** | — |

接入时一次改完风险高于收益，所以先用棘轮：**规则全开，数字记在 `state/quality_budget.json`，只许降不许涨**。
棘轮把「以后再说」变成「只能变好」；现在两个数字都是 0，它自动变成「一行新违规都不许进」。

- 涨了 ⇒ `quality.sh` 直接红，并打印新增的 ruff/mypy 报错。
- 降了 ⇒ 脚本会提示锁定新基线：`python tools/quality_budget.py --update`。
- `--update` 拒绝把基线调高（只接受变好），所以不存在"为了过门而放宽"。

## 存量债：已清零（ruff 0 / mypy 0）

八批历史债全部清完：`B904` / `B007` / `N806` / `SIM117` / `E741` / `UP031` / `SIM115` / `C901`
全部为 0。棘轮现在锁在 **0/0** —— 任何新增违规都会让 `quality.sh` 直接失败。

### 400 条类型债是怎么一次清掉的

按「一处根因解一片」而不是「一条错误一个决定」：

| 根因 | 一处改动 | 解掉 |
|---|---|---|
| `CONCEPTS` / `META_FIELDS` / `DEPRECATED` 没写类型，被推成 `dict[str, Collection[str]]` | 三个声明注解 | ~60 条 `attr-defined` / `index` |
| `RES = [None]` 被推成 `list[None]`，于是每个 `RES[0].say` 都报错 | 改成 `RES: list[_R] = [_R()]` | ~25 条 |
| `_R.result()` 返回 `Any`，13 个 `cmd_*` 全部 `no-any-return` | 给 `_R` 写全字段与方法签名 | ~13 条 |
| 函数签名缺注解（`no-untyped-def` 连带 `no-untyped-call`） | 每个签名补参数/返回类型 | ~200 条 |

顺序原则：**先找声明型根因（一次解一片），再补签名，最后才逐个 `attr-defined`**。
倒过来做会反复动同一批代码 —— 因为每补一个签名，mypy 对别处的推断都会变。

### 别用 `Any` 换指标

收尾阶段把库里的裸 `Any` 从 64 处降到 15 处，换的是真类型：

- `con: sqlite3.Connection`（原来 6 个模块都是 `Any`）
- `s: Session` / `r: _R`（`Protocol` 与 `Session` 写在 `if TYPE_CHECKING` 里）
- `_add_*_parser(sub: _SubParsers)` —— 自己写 12 行 `Protocol`，不引用 argparse 私有类名
- `upsert(appmsgid: str | int, itemidx: int | str, title: str, …) -> None`

剩下的 `Any` 都是真的：JS 求值返回值、`**meta` 关键字、JSON 里的任意值。
**用 `Any` 把数字压到 0 等于把类型检查关掉** —— 那些位置宁可留着 `Any` 也不写假类型。

### 两个自动修复互相打架

`ruff --fix` 的 `B010` 会把 `setattr(模块, "常量名", 替身)` 改回属性赋值，
而 mypy 不允许给类型对象赋值。两边都对：这里必须用 `setattr` 绕开类型的不可赋值性，
所以写 `setattr(...)  # noqa: B010` 定点压住，而不是关掉整条规则。

### 注解的求值时机（3.10 会炸）

`if TYPE_CHECKING:` 里 import 的名字只存在于类型检查期。Python 3.14 默认延迟求值注解（PEP 649），
所以本地全绿；**3.10 会在 import 时就求值并 NameError**。
凡是用了 `TYPE_CHECKING` 的模块，必须带 `from __future__ import annotations`，
且这一行要放在模块 docstring 之后、其它 import 之前（放在文件第一行会让 docstring 变成普通表达式，
`__doc__` 丢失，同时 `ruff` 报 E402）。

## 中文化带来的两条豁免（有理由，不是图省事）

- `RUF001/002/003`（ambiguous-unicode-character）：本仓库正文/注释/文档字符串是中文，
  全角标点（，。：；）是中文排版正确写法。实测 2077 条里没有一条是真正的混淆字符冲突。
- `E501`：行宽由格式化器负责，不需要 lint 再管一次。

## 门抓到过的真 bug（证明它不是摆设）

接入当天，门在存量代码里找到两个会导致**线上错误**的问题：

1. **`analysis.py` FAMILY_SPLIT 文案崩**：格式串 6 个占位符、7 个实参
   ⇒ 走到该分支必抛 `TypeError: not all arguments converted`。
   原有测试只断言 `verdict != "FAMILY_SPLIT"`，**刻意绕开了这条分支**，
   所以 232 个测试全绿也没发现（`ruff F507` 一跑就抓到）。
   已修 + 加回归测试 + 负对照（把 bug 放回去测试必红）。

2. **`mpdt push` 失败也退出 0**：`result()` 恒 `return 0`、`payload()` 恒 `"ok": True`
   ⇒ 推送失败、或「SMTP 接受但未回查确认送达」时，cron / 脚本看到的是**成功**。
   这和 R239「SMTP 接受 ≠ 送达」是同一类事故。
   已修：`ok=False` 同时反映到 exit code 与 `--json` 的 `ok` 字段，并对 `--verify` 未确认分支补 `ok=False`。

另外顺手删了一处**不可达分支**（`family_verdict` 里"只有一个家族"的 return：
函数开头已挡掉 `len(usable) < 2`，家族名是 dict 的键 ⇒ 必然至少剩一个）。

## 一个刻意的偏差（写在这里，免得以后有人以为漏抄）

skill 原版 `scripts/quality.sh` 的第 2、3 行是 `ruff check .` 和 `mypy`，要求 exit 0。
本仓库改成 `python tools/quality_budget.py`（内部仍然跑这两个工具并解析错误数），
因为存量代码不可能一天清零，而 ignore 会让门对新代码失效。
**这条偏差只影响"存量怎么算"，不影响"新代码必须干净"**：
基线的每一次下降，都意味着从此多了这么多行的严格约束。

## 解析层：从 fetcher 里拆出来

`mpdt/fetchers/*.py` 原来一个文件里既有 `fetch()`（WebSocket + CDP）也有解析。拆开之后：

```
mpdt/parsing.py            原始文本 → 结构化（纯，可脱离浏览器验证）
mpdt/fetchers/*.py         只有 fetch()：ensure_token → 发 JS → 把结果交给 parsing
```

旧导入路径仍然有效（`from mpdt.fetchers.publish_list import parse_publish_list`），
所以拆文件没有让任何调用方改代码。

**为什么值得**：解析规则就是口径——哪种结构算「上游改版」、哪些行跳过、字段缺失
回退到哪。这些以前只能靠「起一台登录了公众号的 Chrome」来验证；现在
`tests/test_parsing.py` 用字符串构造输入，26 条用例覆盖全部失败路径。

拆分当场抓到两个真问题：

1. **`mail_subject` 读不了原始 UTF-8 主题**（`charset='unknown-8bit'` 时 `make_header`
   按 ascii 解成乱码）。后果是主题匹配永远不中 —— 东西明明到了，回查说没到，然后重发。
2. **只有 `mpdt/parsing.py` 里那份 `_coerce_row` 保留了「非数值原样不动」的语义**，
   测试把这条固化了：`"'n/a'"` 不会被悄悄洗成 `"n/a"`。

## 覆盖率怎么从 91% 到 97.6%

不是「为了数字写测试」，而是补三类真正会出事的分支：

| 补的是 | 例子 | 为什么值 |
|---|---|---|
| 上游改版的失败路径 | 外层非 JSON / 三件套缺一件 / HTTP 非 200 | 决定运维看到的是「退出码 4 + 原始片段」还是半截结果 |
| 授权与投递的负路径 | 白名单坏掉、IMAP 查不到、认证失败 | 这里出错 = 把没送达记成送达 |
| 判断层炸掉时的兜底 | `advice.advise` 抛异常 | 客户付钱要的是数据；加值层坏了不该整份报告消失 |

门限从 90 提到 **96**：剩下的 2.4% 是 `render` 的空数据分支与 `metrics` 的过滤循环，
补它们要造大量一次性数据，收益低于维护成本。这条线写在这里，免得以后有人以为是漏了。

