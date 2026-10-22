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

## 为什么 lint / 类型用「棘轮」而不是「必须先清零」

首次接入（2026-10-22）时：

| 项 | 首日 | 现在 | 说明 |
|---|---|---|---|
| ruff | 2423 条 | **164** | 其中 2077 条是 RUF001/002/003（中文全角标点，纯噪音，已按理由豁免）；59 条自动修复；其余见下表 |
| mypy (strict) | **836** 条 | **443** | 补齐测试签名 + `cli.main` 返回注解后降 47% |
| 分支覆盖率 | 84.08% | **90.01%** | 262 个测试 |
| 格式 | 37 文件待改 | **0** | — |

剩下的一次改完风险高于收益，但**不能 ignore** —— 一 ignore，规则对新代码也失效，这道门就白接了。
所以改成：**规则全开，数字记在 `state/quality_budget.json`，只许降不许涨**。

- 涨了 ⇒ `quality.sh` 直接红，并打印新增的 ruff/mypy 报错。
- 降了 ⇒ 脚本会提示锁定新基线：`python tools/quality_budget.py --update`。
- `--update` 拒绝把基线调高（只接受变好），所以不存在"为了过门而放宽"。

## 存量债（164 条 ruff / 443 条 mypy）与收敛顺序

按「风险低 × 收益高」排，前三条是下一批要清的：

| 批 | 项 | 条数 | 怎么清 |
|---|---|---|---|
| 1 | `B904` raise 缺 `from` | 10 | 逐处加 `from e`（不影响行为） |
| 2 | `B007` 循环变量未用 | 7 | 改成 `_` |
| 3 | `N806` 函数内非小写变量 | 10 | 重命名中间量（有测试兜底） |
| 4 | `SIM117` 嵌套 with | 5 | 合并 |
| 5 | `E741` 歧义变量名 | 4 | `l` → `line` 等 |
| 6 | `UP031` `%(name)s` 插值 | 55 | **改前必须先补输出文案的断言**：改错会静默改变客户看到的文字 |
| 7 | `SIM115` `open()` 未用 `with` | 48 | 逐个看句柄生命周期（CDP/socket 故意跨函数存活，机械套 `with` 会提前关闭） |
| 8 | `C901` 复杂度 > 10 | 15 | 最大是 `report.generate`（**50**）—— 周报主流程。拆它之前先把分支测试补齐 |

`C901` 的收敛目标就是 skill 的承诺：`ruff check --select C901 .` 干净。
现在之所以没做，是因为拆 `generate`（复杂度 50）必须有足够的分支测试兜底，否则是在最关键的输出路径上盲改。

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
