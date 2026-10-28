"""The release path's invariants.

Every check here guards something that has actually broken, or something whose
absence would only be noticed after publishing. A guard that silently stops
guarding is worse than no guard, so these read the real files.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yml"
TESTS = WORKFLOWS / "tests.yml"
RELEASE = ROOT / "scripts" / "release.sh"
QUALITY = ROOT / "scripts" / "quality.sh"
PYPROJECT = ROOT / "pyproject.toml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def permissions_of(text: str) -> dict[str, str] | None:
    """The workflow-level `permissions:` block as a dict.

    Parsed by hand rather than with PyYAML: this suite runs on the standard
    library alone. The block is a flat mapping of scalars.
    """
    match = re.search(r"^permissions:\n((?:[ \t]+.*\n)+)", text, re.M)
    if not match:
        return None
    declared: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or ":" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.strip().partition(":")
        declared[key.strip()] = value.split("#", 1)[0].strip().strip("'\"")
    return declared


class TestTheReleaseScript(unittest.TestCase):
    """`scripts/release.sh` is what a human runs."""

    def setUp(self) -> None:
        self.script = read(RELEASE)

    def test_it_is_executable(self) -> None:
        self.assertTrue(RELEASE.stat().st_mode & 0o111, "release.sh is not executable")

    def test_it_refuses_a_dirty_tree(self) -> None:
        self.assertIn("git status --porcelain", self.script)

    def test_it_refuses_a_branch_other_than_main(self) -> None:
        self.assertIn('!= "main"', self.script)

    def test_it_refuses_to_release_out_of_sync_with_origin(self) -> None:
        self.assertIn("origin/main", self.script)

    def test_it_runs_the_gate_before_bumping(self) -> None:
        self.assertIn("./scripts/quality.sh", self.script)
        self.assertLess(
            self.script.index("./scripts/quality.sh"),
            self.script.index('say "2. bumping the version"'),
            "the gate must run before the version moves",
        )

    def test_the_publish_is_triggered_by_a_tag(self) -> None:
        self.assertIn('git tag -a "$TAG"', self.script)
        self.assertIn('git push origin "$TAG"', self.script)

    def test_it_bumps_every_file_that_carries_the_version(self) -> None:
        # Two files, and the gate is the referee for both.
        self.assertIn("mpdt", self.script)
        self.assertIn("__init__.py", self.script)
        self.assertIn("pyproject.toml", self.script)

    def test_it_runs_the_version_gate_before_committing(self) -> None:
        self.assertIn("tools/version_gate.py", self.script)
        self.assertLess(
            self.script.index("tools/version_gate.py"), self.script.index("git commit -q")
        )

    def test_it_writes_a_changelog_entry(self) -> None:
        self.assertIn("CHANGELOG.md", self.script)

    def test_it_refuses_an_existing_tag(self) -> None:
        self.assertIn('git rev-parse "$TAG"', self.script)

    def test_a_dry_run_never_publishes(self) -> None:
        # The dry-run branch must exit before the tag is created.
        dry = self.script.index('if [ "$DRY_RUN" = "1" ]')
        tag = self.script.index('git tag -a "$TAG"')
        self.assertLess(dry, tag, "the dry-run branch must come before tagging")
        between = self.script[dry:tag]
        self.assertIn("exit 0", between, "the dry-run branch must exit, not fall through")


class TestThePublishWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(PUBLISH)

    def test_it_runs_only_on_a_tag_or_a_rehearsal(self) -> None:
        self.assertIn("tags: ['v*']", self.text)

    def test_a_manual_dispatch_cannot_publish(self) -> None:
        """`workflow_dispatch` is a rehearsal; only a tag reaches PyPI."""
        publish_job = self.text.split("  publish:", 1)[1]
        self.assertIn("if: github.ref_type == 'tag'", publish_job)

    def test_the_tests_run_first(self) -> None:
        self.assertIn("uses: ./.github/workflows/tests.yml", self.text)
        publish_job = self.text.split("  publish:", 1)[1]
        self.assertIn("needs: build", publish_job)
        build_job = self.text.split("  build:", 1)[1].split("  publish:", 1)[0]
        self.assertIn("needs: test-first", build_job)

    def test_it_does_not_publish_two_at_once(self) -> None:
        self.assertIn("concurrency:", self.text)

    def test_it_checks_the_artifact_not_just_the_metadata(self) -> None:
        self.assertIn("tools/verify_artifact.py", self.text)

    def test_it_installs_the_built_wheel_and_runs_it(self) -> None:
        """Installing the source tree does not prove the artifact works."""
        self.assertIn("dist/*.whl", self.text)
        self.assertIn("mpdt --version", self.text)

    def test_it_publishes_with_oidc(self) -> None:
        publish_job = self.text.split("  publish:", 1)[1]
        self.assertIn("id-token: write", publish_job)
        self.assertIn("pypa/gh-action-pypi-publish", publish_job)

    def test_it_declares_attestations(self) -> None:
        self.assertIn("attestations: true", self.text)

    def test_it_pins_the_build_tooling(self) -> None:
        # An unpinned `build` can change what goes into the wheel between the
        # gate and the artifact.
        self.assertIn("build==", self.text)
        self.assertIn("twine==", self.text)


class TestNoSecretIsNeeded(unittest.TestCase):
    def test_no_workflow_reads_a_pypi_token(self) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            text = read(path)
            self.assertNotIn("secrets.PYPI", text, f"{path.name} reads a PyPI token")
            self.assertNotIn("secrets.TWINE", text, f"{path.name} reads a twine password")

    def test_the_repository_holds_no_publishing_credential(self) -> None:
        # A credential file in the tree would be published.
        for name in (".pypirc", "pypirc"):
            self.assertFalse((ROOT / name).exists(), f"{name} is in the repository")


class TestTheWorkflowsArePinned(unittest.TestCase):
    """OpenSSF Scorecard's Pinned-Dependencies: a tag can be moved."""

    def test_every_action_is_pinned_to_a_commit_sha(self) -> None:
        pattern = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)(@[\w.]+)?")
        for path in WORKFLOWS.glob("*.yml"):
            for match in pattern.finditer(read(path)):
                action, ref = match.group(1), match.group(2) or ""
                if action.startswith("./"):
                    continue  # a local reusable workflow, not a dependency
                self.assertRegex(
                    ref,
                    r"@[0-9a-f]{40}$",
                    f"{path.name}: {action}{ref} is not pinned to a commit SHA",
                )

    def test_no_workflow_asks_for_write_all(self) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            text = read(path)
            self.assertNotIn("write-all", text, f"{path.name} asks for write-all")
            declared = permissions_of(text)
            if declared is not None:
                for key, value in declared.items():
                    self.assertNotIn("*", value, f"{path.name}: wildcard permission {key}")


class TestTheVersionGateIsWired(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(TESTS)

    def test_it_runs_on_a_tag_push(self) -> None:
        """A release is exactly when the two version files must agree."""
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", self.text)

    def test_a_tag_push_requires_the_tag_to_match_the_version(self) -> None:
        self.assertIn("GATE_EXPECT_TAG", self.text)

    def test_it_fetches_tags_forcefully(self) -> None:
        # actions/checkout takes tags as lightweight refs while release tags
        # are annotated objects, so a plain `git fetch --tags` is rejected.
        self.assertIn("git fetch --tags --force", self.text)


class TestThePackageMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.pyproject = read(PYPROJECT)

    def test_the_pyproject_parses(self) -> None:
        """It did not, for five days: a duplicate `keywords` key."""
        try:
            import tomllib  # type: ignore[import-not-found]
        except ModuleNotFoundError:  # Python 3.10
            self.skipTest("tomllib needs Python 3.11+")
        tomllib.loads(self.pyproject)

    def test_no_key_is_declared_twice_in_the_project_table(self) -> None:
        # TOML rejects this outright, but the error message points at the
        # second occurrence and reads like a value problem.
        project = self.pyproject.split("[project]", 1)[1].split("\n[", 1)[0]
        keys = [
            line.split("=", 1)[0].strip()
            for line in project.splitlines()
            if "=" in line and not line.strip().startswith("#") and not line.startswith(" ")
        ]
        duplicates = {k for k in keys if keys.count(k) > 1}
        self.assertFalse(duplicates, f"duplicate keys in [project]: {sorted(duplicates)}")

    def test_it_declares_the_python_versions_ci_tests(self) -> None:
        self.assertIn("requires-python", self.pyproject)

    def test_the_readme_is_the_long_description(self) -> None:
        self.assertIn("readme", self.pyproject)


if __name__ == "__main__":
    unittest.main()
