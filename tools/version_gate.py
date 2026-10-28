#!/usr/bin/env python3
"""version_gate.py — dual-file version consistency + monotonic bump gate.

Rule (from R113 e1, T2/T8 family variant): in projects carrying the version in
TWO files (pyproject.toml + mpdt/__init__.py), a feature commit that edits
__init__ from a stale baseline can silently regress the version while all tests
stay green (exactly what happened between v0.2.3/v0.2.4 — commit 1cdd7e4
reverted __init__ to 0.2.2 while pyproject said 0.2.3).

Gate (exit codes):
  0 = files agree AND version > highest semver git tag
  1 = violation, message on stderr names both locations / what to do
  2 = tool error (missing file, no tags, bad git)

Usage:
  python tools/version_gate.py [repo_root]   (default: cwd)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

INIT_RE = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.M)
PYPROJECT_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.M)


def parse_semver(v: str) -> tuple[int, int, int] | None:
    """'v1.2.3'/'1.2.3' -> (1,2,3); returns None for non-semver strings."""
    v = v.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def read_versions(root: str) -> tuple[str, str]:
    pp = os.path.join(root, "pyproject.toml")
    init = os.path.join(root, "mpdt", "__init__.py")
    for p in (pp, init):
        if not os.path.isfile(p):
            sys.stderr.write(f"version_gate: missing {p}\n")
            sys.exit(2)
    pp_text = Path(pp).read_text(encoding="utf-8")
    init_text = Path(init).read_text(encoding="utf-8")
    m1 = PYPROJECT_RE.search(pp_text)
    m2 = INIT_RE.search(init_text)
    if not m1 or not m2:
        sys.stderr.write(
            f"version_gate: cannot parse version (pyproject={m1 and m1.group(1)} __init__={m2 and m2.group(1)})\n"
        )
        sys.exit(2)
    return m1.group(1).strip(), m2.group(1).strip()


def highest_tag(root: str) -> tuple[str, tuple[int, int, int]]:
    out = subprocess.run(
        ["git", "-C", root, "tag", "--list"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        sys.stderr.write(f"version_gate: git tag failed: {out.stderr.strip()}\n")
        sys.exit(2)
    best, best_sem = None, None
    for line in out.stdout.split():
        sem = parse_semver(line)
        if sem and (best_sem is None or sem > best_sem):
            best, best_sem = line, sem
    if best is None or best_sem is None:
        sys.stderr.write("version_gate: no semver tags found in repo\n")
        sys.exit(2)
    return best, best_sem


def check(root: str, allow_eq: bool = False, expect_tag: str | None = None) -> tuple[int, str]:
    """Returns (exit_code, message).

    allow_eq: version == highest tag is OK (PR / tag-push mode).
    expect_tag: when set (tag-push publishing), the version MUST equal this tag.
        Rationale: the package is built from the source tree, but the tag is a
        separate object. Pushing `v0.2.7` onto a tree whose version says 0.2.8
        would publish 0.2.8 under a v0.2.7 tag — the artifact and its label
        disagree, and nothing else in the pipeline would notice.
    """
    v_pp, v_init = read_versions(root)
    tag_name, tag_sem = highest_tag(root)
    sem_pp, sem_init = parse_semver(v_pp), parse_semver(v_init)
    if sem_pp is None or sem_init is None or tag_sem is None:
        msg = f"version_gate: non-semver version (pyproject={v_pp} __init__={v_init})"
        print(msg, file=sys.stderr)
        return 2, msg
    if v_pp != v_init or sem_pp != sem_init:
        msg = (
            f"version_gate: VERSION MISMATCH — pyproject.toml={v_pp} but mpdt/__init__.py={v_init}.\n"
            "Fix: align both files before commit (release commits are the ONLY place version changes)."
        )
        print(msg, file=sys.stderr)
        return 1, msg
    if expect_tag:
        exp_sem = parse_semver(expect_tag)
        if exp_sem is None:
            msg = f"version_gate: GATE_EXPECT_TAG={expect_tag!r} is not semver"
            print(msg, file=sys.stderr)
            return 2, msg
        if exp_sem != sem_pp:
            msg = (
                f"version_gate: TAG/VERSION MISMATCH — pushed tag {expect_tag} but the version "
                f"files say {v_pp}.\n"
                "Fix: bump BOTH files and tag that commit; the tag must name the version being "
                "released, otherwise the artifact and its label disagree."
            )
            print(msg, file=sys.stderr)
            return 1, msg
    if sem_pp < tag_sem or (sem_pp == tag_sem and not allow_eq):
        mode = "EQUAL" if sem_pp == tag_sem else "BELOW"
        msg = (
            "version_gate: VERSION {} — pyproject.toml and mpdt/__init__.py agree at {} but highest tag is {}.\n"
            "Fix: if this commit changes mpdt, bump BOTH files to > {} (version bump only in release commit); "
            "{}".format(
                mode,
                v_pp,
                tag_name,
                tag_name,
                "if this is a docs/CI-only change not touching mpdt/, this failure means a bump is expected — "
                "or run with GATE_ALLOW_EQ=1 on PRs."
                if not allow_eq
                else "docs/CI-only PRs: coordinate a release bump or keep mpdt/ unchanged.",
            )
        )
        print(msg, file=sys.stderr)
        return 1, msg
    print(
        "version_gate OK: {} (files agree, {} {})".format(
            v_pp, ">" if sem_pp > tag_sem else "==", tag_name
        )
    )
    return 0, "OK"


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    allow_eq = os.environ.get("GATE_ALLOW_EQ") == "1"
    # 推 tag 发版时由 CI 传入 github.ref_name（如 v0.2.7）：
    # 要求 tag 名与被发布的版本号一致，否则包和标签会对不上。
    expect_tag = os.environ.get("GATE_EXPECT_TAG") or None
    code, _ = check(root, allow_eq=allow_eq, expect_tag=expect_tag)
    sys.exit(code)
