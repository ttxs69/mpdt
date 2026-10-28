#!/usr/bin/env python3
"""readme_audit.py — audit local README.md vs PyPI rendered description.

调用 PyPI JSON API 取 info.description，与本地 README.md 比对：
- 字符数差异
- 行级 unified diff（若差异）

Usage:
  python3 tools/readme_audit.py                # mpdt 默认
  python3 tools/readme_audit.py --package mpdt --repo .
  python3 tools/readme_audit.py --package mpdt --repo . --strict-diff

退出码: 0=完全一致 / 1=差异 / 4=网络错误
"""

import argparse
import difflib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fetch_pypi_desc(package, timeout=15):
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "mpdt-readme-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PyPI HTTP {e.code} for {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"PyPI URL error for {url}: {e.reason}") from e
    desc = data.get("info", {}).get("description")
    return desc


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--package", default="mpdt")
    p.add_argument("--repo", default=".", help="Repo root containing README.md")
    p.add_argument("--strict-diff", action="store_true", help="非 0 退出码当有差异")
    args = p.parse_args()
    readme_path = Path(args.repo) / "README.md"
    if not readme_path.is_file():
        print(f"[ERROR] no README.md at {readme_path}", file=sys.stderr)
        return 4
    try:
        pypi_desc = fetch_pypi_desc(args.package)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 4
    if pypi_desc is None:
        print(f"[ERROR] no description on PyPI for {args.package}", file=sys.stderr)
        return 4
    local_readme = readme_path.read_text(encoding="utf-8")
    if pypi_desc.strip() == local_readme.strip():
        print(f"[OK] {args.package} README.md == PyPI description (chars={len(local_readme)})")
        return 0
    # Diff
    print(f"[DIFF] {args.package} README.md vs PyPI description:")
    print(f"  local chars: {len(local_readme)}")
    print(f"  pypi  chars: {len(pypi_desc)}")
    diff = list(
        difflib.unified_diff(
            pypi_desc.splitlines(),
            local_readme.splitlines(),
            lineterm="",
            fromfile="pypi",
            tofile="local",
        )
    )
    if diff:
        print("\n".join(diff[:50]))
        if len(diff) > 50:
            print(f"... (+{len(diff) - 50} more diff lines)")
    if args.strict_diff:
        return 1
    # 非严格模式：有差异仍然退出 0（仅记录用）
    return 0


if __name__ == "__main__":
    sys.exit(main())
