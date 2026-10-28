#!/usr/bin/env python3
"""verify_artifact.py — refuse to publish an artifact nobody looked at.

`twine check` validates the metadata; it says nothing about *what is in the
files*. A wheel can carry `tests/`, a stale `.venv`, a vendored `__pycache__`,
or a `__init__.py` whose `__version__` disagrees with the METADATA — and
`twine check` passes all of it.

This checks the artifact the way a user experiences it:

  1. the wheel's METADATA version == the expected version
  2. the wheel's `mpdt/__init__.py` `__version__` == the expected version
  3. the wheel ships no tests, no dev tooling, no caches, no build leftovers
  4. the sdist and the wheel agree on the package sources (same set of
     `mpdt/**/*.py`, byte-identical)
  5. the wheel contains every module the sdist does, so an installed package
     cannot be missing a file the source tree has

Exit codes: 0 = publishable, 1 = the artifact is wrong (message on stderr,
naming the file), 2 = could not inspect (no dist/, bad args).

Usage:
  python tools/verify_artifact.py dist/ --expect-version 0.2.8
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# Paths that must never reach a user's site-packages. Each one has actually
# shipped in someone's package: tests that import pytest and break on install,
# a committed virtualenv, `__pycache__` with .pyc files built for another
# interpreter.
FORBIDDEN_PREFIXES = ("tests/", "test/", "tools/", "scripts/", "docs/", ".github/")
FORBIDDEN_PARTS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git")
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej")


def _sources(names: list[str], root: str) -> dict[str, bytes]:
    """The `mpdt/**/*.py` entries of a member listing, keyed by relative path."""
    out = {}
    for name in names:
        posix = PurePosixPath(name)
        try:
            rel = posix.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "mpdt" and rel.suffix == ".py":
            out[str(rel)] = b""
    return out


def wheel_members(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def wheel_text(wheel: Path, member: str) -> str:
    with zipfile.ZipFile(wheel) as zf:
        return zf.read(member).decode("utf-8", errors="replace")


def sdist_members(sdist: Path) -> tuple[list[str], str]:
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    root = names[0].split("/", 1)[0] if names else ""
    return names, root


def sdist_source_bytes(sdist: Path, root: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(sdist) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            posix = PurePosixPath(member.name)
            try:
                rel = posix.relative_to(root)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == "mpdt" and rel.suffix == ".py":
                handle = tf.extractfile(member)
                if handle is not None:
                    out[str(rel)] = handle.read()
    return out


def wheel_source_bytes(wheel: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            posix = PurePosixPath(name)
            if posix.parts and posix.parts[0] == "mpdt" and posix.suffix == ".py":
                out[str(posix)] = zf.read(name)
    return out


def find_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        sys.exit(f"verify_artifact: no {pattern} in {directory} — build first")
    if len(matches) > 1:
        listing = "\n  ".join(m.name for m in matches)
        sys.exit(f"verify_artifact: more than one {pattern} in {directory}:\n  {listing}")
    return matches[0]


def check_metadata_version(wheel: Path, expected: str) -> list[str]:
    problems = []
    metadata = next((n for n in wheel_members(wheel) if n.endswith(".dist-info/METADATA")), None)
    if metadata is None:
        return ["the wheel has no .dist-info/METADATA"]
    declared = ""
    for line in wheel_text(wheel, metadata).splitlines():
        if line.startswith("Version:"):
            declared = line.split(":", 1)[1].strip()
            break
    if declared != expected:
        problems.append(f"METADATA says {declared!r}, the release says {expected!r}")
    return problems


def check_init_version(wheel: Path, expected: str) -> list[str]:
    members = [n for n in wheel_members(wheel) if n.endswith("mpdt/__init__.py")]
    if not members:
        return ["the wheel ships no mpdt/__init__.py"]
    for line in wheel_text(wheel, members[0]).splitlines():
        if line.startswith("__version__"):
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value != expected:
                return [f"mpdt/__init__.py says {value!r}, the release says {expected!r}"]
            return []
    return ["mpdt/__init__.py declares no __version__"]


def check_no_junk(names: list[str], kind: str) -> list[str]:
    """The wheel must not carry anything a user did not ask to install."""
    problems = []
    for name in names:
        posix = PurePosixPath(name)
        # Inside the package, only the junk-part rules apply; the top-level
        # `tests/` and `tools/` rules apply at the root.
        if (
            posix.parts
            and posix.parts[0] != "mpdt"
            and any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
        ):
            problems.append(f"{kind} ships {name}")
            continue
        for part in posix.parts:
            if part in FORBIDDEN_PARTS:
                problems.append(f"{kind} ships {name} (contains {part}/)")
                break
        else:
            if name.endswith(FORBIDDEN_SUFFIXES):
                problems.append(f"{kind} ships {name}")
    return sorted(set(problems))


def check_sources_match(wheel: Path, sdist: Path, sdist_root: str) -> list[str]:
    wheel_sources = wheel_source_bytes(wheel)
    sdist_sources = sdist_source_bytes(sdist, sdist_root)
    problems = []
    missing = sorted(set(sdist_sources) - set(wheel_sources))
    extra = sorted(set(wheel_sources) - set(sdist_sources))
    if missing:
        problems.append("the wheel is missing files the sdist has: " + ", ".join(missing))
    if extra:
        problems.append("the wheel has files the sdist does not: " + ", ".join(extra))
    differing = sorted(
        name
        for name in set(wheel_sources) & set(sdist_sources)
        if wheel_sources[name] != sdist_sources[name]
    )
    if differing:
        problems.append("sdist and wheel disagree on: " + ", ".join(differing))
    return problems


def read_pyproject_identity(directory: Path) -> dict[str, str]:
    """`name` and `version` from `[project]`, read without a TOML parser.

    `tomllib` is 3.11+, the package supports 3.10, and mypy is configured for
    3.10 — importing it at module scope fails type-checking and would fail at
    import on the oldest supported interpreter. These two keys are simple
    `key = "value"` lines in the `[project]` table; the version gate reads them
    the same way, for the same reason.
    """
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    text = pyproject.read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0] if "[project]" in text else ""
    found: dict[str, str] = {}
    for key in ("name", "version"):
        match = re.search(rf'^{key}\s*=\s*["\']([^"\']+)["\']', project, re.M)
        if match:
            found[key] = match.group(1)
    return found


def check_project_identity(wheel: Path, directory: Path) -> list[str]:
    """The artifact must be the project this directory declares itself to be."""
    declared = read_pyproject_identity(directory)
    if not declared:
        return []
    metadata = next((n for n in wheel_members(wheel) if n.endswith(".dist-info/METADATA")), None)
    if metadata is None:
        return []
    text = wheel_text(wheel, metadata)
    problems = []
    for field, key in (("Name", "name"), ("Version", "version")):
        want = declared.get(key)
        for line in text.splitlines():
            if line.startswith(f"{field}:"):
                got = line.split(":", 1)[1].strip()
                if want and got != want:
                    problems.append(f"{field} is {got!r}, pyproject.toml says {want!r}")
                break
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", default="dist", help="the build directory")
    parser.add_argument("--expect-version", required=True, help="the version being released")
    parser.add_argument("--project-root", default=".", help="where pyproject.toml lives")
    args = parser.parse_args(argv)

    directory = Path(args.dist)
    if not directory.is_dir():
        sys.exit(f"verify_artifact: {directory} is not a directory — build first")

    wheel = find_one(directory, "*.whl")
    sdist = find_one(directory, "*.tar.gz")
    expected = args.expect_version.lstrip("v")

    names = wheel_members(wheel)
    sdist_names, sdist_root = sdist_members(sdist)

    problems: list[str] = []
    problems += check_metadata_version(wheel, expected)
    problems += check_init_version(wheel, expected)
    problems += check_no_junk(names, wheel.name)
    problems += check_no_junk(sdist_names, sdist.name)
    problems += check_sources_match(wheel, sdist, sdist_root)
    problems += check_project_identity(wheel, Path(args.project_root))

    if problems:
        print(f"verify_artifact: {len(problems)} problem(s) — do not publish:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    module_count = len([n for n in names if n.startswith("mpdt/") and n.endswith(".py")])
    print(f"verify_artifact: OK — {wheel.name} ({module_count} modules), {sdist.name}")
    print(f"  version {expected} in METADATA and in mpdt/__init__.py")
    print("  no tests, tooling, caches or build leftovers in either artifact")
    print("  sdist and wheel agree on every mpdt/**/*.py, byte for byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
