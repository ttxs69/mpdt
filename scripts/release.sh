#!/usr/bin/env bash
# Release a version of mpdt to PyPI, through CI.
#
#   ./scripts/release.sh patch        # 0.2.7 → 0.2.8
#   ./scripts/release.sh minor        # 0.2.7 → 0.3.0
#   ./scripts/release.sh major        # 0.2.7 → 1.0.0
#   ./scripts/release.sh --version 0.3.0rc1
#   ./scripts/release.sh --dry-run    # build and check, publish nothing
#
# The order follows the same practice as the npm release path, and the same
# sources: a human says which version, CI does the publishing, and nothing is
# published on a merge to the default branch.
#
#   1. safety checks   clean tree, on main, in sync, the whole gate green
#   2. prepare         bump the version in every file that carries it, and
#                      write the changelog entry
#   3. publish         commit, tag, push → CI builds and publishes to PyPI
#
# Publishing uses PyPI trusted publishing (OIDC): the repository holds no
# API token, and PyPI attaches an attestation to every upload.
set -euo pipefail
cd "$(dirname "$0")/.."

BUMP=""
EXPLICIT=""
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    patch|minor|major) BUMP="$arg" ;;
    --version) shift; EXPLICIT="${1:-}" ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
  shift || true
done

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m!! %s\033[0m\n' "$1" >&2; exit 1; }

# The same interpreter resolution as scripts/quality.sh: a local .venv if
# there is one, whatever PYTHON says, otherwise python3.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

CURRENT=$("$PY" -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

# ── 1. safety checks ────────────────────────────────────────────────────────
say "1. safety checks"

if [ -n "$(git status --porcelain)" ]; then
  die "the working tree is not clean. Commit or stash first:
$(git status --short)"
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
  die "releases are cut from main, not $(git rev-parse --abbrev-ref HEAD)"
fi
git fetch --quiet origin main --tags
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  die "main is not in sync with origin/main. Push first."
fi
echo "   tree clean, on main, in sync with origin"

say "1b. the full gate"
./scripts/quality.sh
echo "   gate green"

# ── 2. prepare ──────────────────────────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  say "2. dry run — building ${CURRENT} without publishing"
  rm -rf dist build
  "$PY" -m build 2>&1 | tail -5
  "$PY" -m pip install -q --upgrade "twine==7.0.0" 2>&1 | tail -2
  "$PY" -m twine check --strict dist/*
  "$PY" tools/verify_artifact.py dist --expect-version "$CURRENT"
  say "2b. what would be published"
  ls -la dist/
  exit 0
fi

say "2. bumping the version"
if [ -n "$EXPLICIT" ]; then
  NEW_VERSION="$EXPLICIT"
elif [ -n "$BUMP" ]; then
  NEW_VERSION=$("$PY" - "$CURRENT" "$BUMP" <<'PY'
import re, sys
current, bump = sys.argv[1], sys.argv[2]
m = re.match(r"^(\d+)\.(\d+)\.(\d+)", current)
if not m:
    sys.exit(f"cannot bump a non-semver version: {current}")
major, minor, patch = (int(x) for x in m.groups())
if bump == "major":
    print(f"{major + 1}.0.0")
elif bump == "minor":
    print(f"{major}.{minor + 1}.0")
else:
    print(f"{major}.{minor}.{patch + 1}")
PY
)
else
  die "say what kind of release this is: patch, minor, major, or --version X.Y.Z"
fi

# The version lives in two files. `tools/version_gate.py` is the referee; this
# script writes both, and the gate re-checks them afterwards.
"$PY" - "$NEW_VERSION" <<'PY'
import re, sys, pathlib

version = sys.argv[1]
root = pathlib.Path(".")

init = root / "mpdt" / "__init__.py"
text = init.read_text(encoding="utf-8")
text = re.sub(r'^__version__\s*=\s*["\'][^"\']+["\']', f'__version__ = "{version}"', text, count=1, flags=re.M)
init.write_text(text, encoding="utf-8")

pyproject = root / "pyproject.toml"
text = pyproject.read_text(encoding="utf-8")
text = re.sub(r'^version\s*=\s*["\'][^"\']+["\']', f'version = "{version}"', text, count=1, flags=re.M)
pyproject.write_text(text, encoding="utf-8")
print(f"   wrote {version} into pyproject.toml and mpdt/__init__.py")
PY

TAG="v${NEW_VERSION}"
echo "   ${CURRENT} → ${NEW_VERSION}"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  git checkout -- . 2>/dev/null || true
  die "tag $TAG already exists — pick another version"
fi

# The changelog entry is part of the release, not an afterthought.
say "2b. the changelog entry"
CHANGELOG="CHANGELOG.md"
if [ ! -f "$CHANGELOG" ]; then
  printf '# Changelog\n\n## %s (%s)\n\n- _Describe this release._\n' \
    "$NEW_VERSION" "$(date +%Y-%m-%d)" > "$CHANGELOG"
  echo "   created $CHANGELOG with a section for ${NEW_VERSION}"
else
  if grep -q "^## ${NEW_VERSION}" "$CHANGELOG"; then
    echo "   a section for ${NEW_VERSION} exists — edit it to say what changed:"
    echo
    sed -n "/^## ${NEW_VERSION}/,/^## /p" "$CHANGELOG" | head -20
    echo
    read -r -p "   press Enter to accept, or Ctrl-C to stop: " _
  else
    printf '## %s (%s)\n\n- _Describe this release._\n\n' \
      "$NEW_VERSION" "$(date +%Y-%m-%d)" | cat - "$CHANGELOG" > "$CHANGELOG.tmp"
    mv "$CHANGELOG.tmp" "$CHANGELOG"
    echo "   added a section for ${NEW_VERSION} — edit it to say what changed:"
    echo
    sed -n "/^## ${NEW_VERSION}/,/^## /p" "$CHANGELOG" | head -20
    echo
    read -r -p "   press Enter to accept, or Ctrl-C to stop: " _
  fi
fi

# The referee has the last word, on the tree as it now stands.
say "2c. the version gate"
"$PY" tools/version_gate.py . || die "the version gate refused the release"

git add -A
git commit -q -m "release: ${NEW_VERSION}

$(sed -n "/^## ${NEW_VERSION}/,/^## /p" "$CHANGELOG" | sed '1d;$d' | sed '/^$/d' | head -20)"

# ── 3. publish ──────────────────────────────────────────────────────────────
say "3. tagging and pushing"
echo "   → tagging ${TAG}"
git tag -a "$TAG" -m "mpdt ${NEW_VERSION}"
git push origin main
git push origin "$TAG"
echo
echo "✓ pushed ${TAG} — CI will build and publish to PyPI."
echo "  watch:  gh run list --workflow publish.yml"
echo "  verify: python -m pip index versions mpdt"
