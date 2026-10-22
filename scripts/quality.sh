#!/usr/bin/env bash
# 唯一质量门入口（python-quality-gate）：本地与 CI 跑同一个文件。
# 顺序固定：快检查在前，测试在最后 —— 覆盖率/测试失败绝不能盖住类型/lint 错误。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 解释器解析：本地有 .venv 就用它，CI 里显式传 PYTHON 或已激活 venv。
# 为什么需要这一步：`python` 在有些环境里指向系统解释器，直接跑会 Missing ruff/mypy，
# 而报错信息看起来像"门坏了"，不是"环境没装"。
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
echo "[quality] python = $PY"

# 1) 格式：必须完全符合（无存量债）
"$PY" -m ruff format --check .

# 2) lint + 类型：存量债用**棘轮**，不用 ignore。
#    为什么不是 `ruff check .` + `mypy`（skill 原版的两行）：
#    首次接入时 ruff 剩 164 条、mypy(strict) 剩 636 条，全是 4000 行老代码的存量。
#    要么 ignore（规则对新代码也失效，门就白接了），要么一次性改完（风险高于收益）。
#    第三条路是棘轮：把今天的数字写进基线，**只许降不许涨**。
#    规则全开、债务可见、回归必红。收敛顺序见 QUALITY.md。
"$PY" tools/quality_budget.py

# 3) 测试 + 分支覆盖率（fail_under 在 pyproject 里，只写一次）
"$PY" -m pytest --cov --cov-report=term-missing
