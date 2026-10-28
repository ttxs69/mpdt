#!/usr/bin/env python3
"""质量门预算（存量债的**棘轮**）—— 只允许变好，不允许变差。

**为什么需要它（R68）**
python-quality-gate 的目标是 `mypy`（strict）exit 0。mpdt 是 4000 行存量代码，
第一次接入时 mypy strict 报 **604 个错误 / 32 个文件**。一次性改完不现实，
但"先关掉 strict"等于让这道门从此不再有意义。

⇒ 用棘轮（ratchet）：把今天的数字写进基线，**任何上涨都失败**，
下降则自动更新基线。这样门一直有效，债务单调收敛。

**它查什么**
1. `ruff check` 的错误数（应为 0 —— ruff 侧已清零）
2. `mypy` 的错误数（基线见 quality_budget.json，只许降）
3. 覆盖率下限（写在 pyproject 的 fail_under，由 pytest-cov 自己执行）

**用法**
    python3 tools/quality_budget.py            # 检查（超过基线 ⇒ exit 1）
    python3 tools/quality_budget.py --update   # 把当前值写成新基线（只在数字变小时用）

**退出码** 0 通过 / 1 回归 / 2 工具跑不起来
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "quality_budget.json"
PY = sys.executable


def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def count_ruff() -> tuple[int, str]:
    rc, out = _run([PY, "-m", "ruff", "check", "."])
    m = re.search(r"Found (\d+) error", out)
    n = int(m.group(1)) if m else (0 if rc == 0 else -1)
    return n, out


def count_mypy() -> tuple[int, str]:
    rc, out = _run([PY, "-m", "mypy"])
    m = re.search(r"Found (\d+) error", out)
    n = int(m.group(1)) if m else (0 if rc == 0 else -1)
    return n, out


def load() -> dict[str, int]:
    if not BASELINE.exists():
        return {}
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(d: dict[str, int]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _report_regression(regressed: dict[str, Any], ruff_out: str, mypy_out: str) -> int:
    """打印回归明细（带最后 12 行工具输出），返回 1。"""
    print("❌ 质量债上涨了（棘轮只许下降）：")
    for k, (b, v) in regressed.items():
        print(f"   {k}: {b} → {v}  (+{v - b})")
    if "ruff" in regressed:
        print("\n--- ruff ---")
        print("\n".join(ruff_out.strip().splitlines()[-12:]))
    if "mypy" in regressed:
        print("\n--- mypy ---")
        print("\n".join(mypy_out.strip().splitlines()[-12:]))
    return 1


def _update_baseline(cur: dict[str, Any], base: dict[str, Any]) -> int:
    """--update：只许把基线调小。"""
    worse = {k: (base.get(k, 10**9), v) for k, v in cur.items() if v > base.get(k, 10**9)}
    if worse:
        for k, (b, v) in worse.items():
            print(f"❌ 拒绝把 {k} 基线从 {b} 调到更差的 {v}")
        return 1
    save(cur)
    print(f"✅ 基线已更新：{cur}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="质量门预算棘轮：存量债只许降不许涨")
    ap.add_argument("--update", action="store_true", help="用当前值覆盖基线（只在数字变小时可用）")
    a = ap.parse_args()

    ruff_n, ruff_out = count_ruff()
    mypy_n, mypy_out = count_mypy()
    if ruff_n < 0 or mypy_n < 0:
        print("❌ 工具跑不起来，无法判定。")
        print(ruff_out[-600:])
        print(mypy_out[-600:])
        return 2

    cur = {"ruff": ruff_n, "mypy": mypy_n}
    base = load()

    if a.update:
        return _update_baseline(cur, base)

    if not base:
        print("⚠️ 还没有基线，先建一份：--update")
        print(f"   当前：{cur}")
        return 1

    regressed = {k: (base.get(k, 0), v) for k, v in cur.items() if v > base.get(k, 0)}
    improved = {k: (base.get(k, 0), v) for k, v in cur.items() if v < base.get(k, 0)}

    if regressed:
        return _report_regression(regressed, ruff_out, mypy_out)

    msg = "✅ 预算通过：" + "；".join(
        f"{k} {cur[k]}" + (f"（基线 {base[k]}，已降 {base[k] - cur[k]}）" if k in improved else "")
        for k in cur
    )
    print(msg)
    if improved:
        print(f"   建议锁定新基线：python3 tools/quality_budget.py --update（{cur}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
