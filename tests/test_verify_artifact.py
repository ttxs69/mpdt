"""The artifact verifier must refuse bad artifacts, not just accept good ones.

`twine check` passing is not evidence. These tests build wheel/sdist pairs that
are wrong in the ways packages have actually been wrong, and assert that
`tools/verify_artifact.py` names the problem and exits non-zero.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "verify_artifact.py"


def load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_artifact", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


va = load_verifier()

GOOD_SOURCES: dict[str, str] = {
    "mpdt/__init__.py": '__version__ = "1.2.3"\n',
    "mpdt/cli.py": "def main() -> int:\n    return 0\n",
}


def write_wheel(
    path: Path,
    *,
    version: str = "1.2.3",
    init_version: str = "1.2.3",
    extra: tuple[tuple[str, str], ...] = (),
    sources: dict[str, str] | None = None,
) -> None:
    body = dict(GOOD_SOURCES if sources is None else sources)
    # `init_version` must actually reach the file, or a test that asks for a
    # mismatch silently gets a matching wheel and passes for the wrong reason.
    body["mpdt/__init__.py"] = f'__version__ = "{init_version}"\n'
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in body.items():
            zf.writestr(name, text)
        zf.writestr(
            f"mpdt-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: mpdt\nVersion: {version}\n",
        )
        for extra_name, extra_text in extra:
            zf.writestr(extra_name, extra_text)


def write_sdist(
    path: Path,
    *,
    version: str = "1.2.3",
    sources: dict[str, str] | None = None,
) -> None:
    body = GOOD_SOURCES if sources is None else sources
    with tarfile.open(path, "w:gz") as tf:
        for name, text in body.items():
            data = text.encode()
            info = tarfile.TarInfo(f"mpdt-{version}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


class ArtifactCase(unittest.TestCase):
    _tmp: tempfile.TemporaryDirectory[str]
    dir: Path
    project: Path
    dist: Path

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.project = self.dir / "project"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text(
            '[project]\nname = "mpdt"\nversion = "1.2.3"\n', encoding="utf-8"
        )
        self.dist = self.dir / "dist"
        self.dist.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(
        self,
        *,
        version: str = "1.2.3",
        init_version: str = "1.2.3",
        extra: tuple[tuple[str, str], ...] = (),
        wheel_sources: dict[str, str] | None = None,
        sdist_sources: dict[str, str] | None = None,
    ) -> None:
        write_wheel(
            self.dist / "mpdt-1.2.3-py3-none-any.whl",
            version=version,
            init_version=init_version,
            extra=extra,
            sources=wheel_sources,
        )
        write_sdist(self.dist / "mpdt-1.2.3.tar.gz", version="1.2.3", sources=sdist_sources)

    def verify(self, expect: str = "1.2.3") -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = va.main(
                    [
                        str(self.dist),
                        "--expect-version",
                        expect,
                        "--project-root",
                        str(self.project),
                    ]
                )
            except SystemExit as exc:  # the "could not inspect" path
                # `sys.exit("message")` carries the message on `code`; Python
                # would print it to stderr, but we caught the exception.
                if isinstance(exc.code, int):
                    code = exc.code
                else:
                    code = 2
                    err.write(str(exc.code or ""))
        return code, out.getvalue() + err.getvalue()


class TestItAcceptsAGoodArtifact(ArtifactCase):
    def test_a_matching_pair_passes(self) -> None:
        self.build()
        code, output = self.verify()
        self.assertEqual(code, 0, output)
        self.assertIn("OK", output)


class TestItRefusesABadArtifact(ArtifactCase):
    def test_init_version_disagreeing_with_metadata(self) -> None:
        """The classic: METADATA says one thing, `__version__` another."""
        self.build(init_version="0.9.9")
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("mpdt/__init__.py says '0.9.9'", output)

    def test_metadata_version_disagreeing_with_the_release(self) -> None:
        self.build(version="1.2.2")
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("METADATA says '1.2.2'", output)

    def test_a_wheel_carrying_the_test_suite(self) -> None:
        """Shipping `tests/` installs pytest as a runtime import."""
        self.build(extra=(("tests/test_cli.py", "import pytest\n"),))
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("ships tests/test_cli.py", output)

    def test_a_wheel_carrying_bytecode(self) -> None:
        self.build(extra=(("mpdt/__pycache__/cli.cpython-312.pyc", "\x00"),))
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("__pycache__", output)

    def test_a_wheel_carrying_dev_tooling(self) -> None:
        self.build(extra=(("tools/release_helper.py", "x = 1\n"),))
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("ships tools/release_helper.py", output)

    def test_a_module_the_sdist_has_and_the_wheel_lacks(self) -> None:
        """An installed package missing a file the source tree has."""
        write_wheel(self.dist / "mpdt-1.2.3-py3-none-any.whl")
        write_sdist(
            self.dist / "mpdt-1.2.3.tar.gz",
            sources={**GOOD_SOURCES, "mpdt/extra.py": "y = 2\n"},
        )
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("missing files the sdist has", output)
        self.assertIn("mpdt/extra.py", output)

    def test_a_module_that_differs_between_the_two(self) -> None:
        write_wheel(
            self.dist / "mpdt-1.2.3-py3-none-any.whl",
            sources={**GOOD_SOURCES, "mpdt/cli.py": "def main() -> int:\n    return 1\n"},
        )
        write_sdist(self.dist / "mpdt-1.2.3.tar.gz")
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("disagree on: mpdt/cli.py", output)

    def test_the_artifact_of_a_different_project(self) -> None:
        """Publishing mpdt's wheel as another package, or vice versa."""
        self.build()
        (self.project / "pyproject.toml").write_text(
            '[project]\nname = "not-mpdt"\nversion = "1.2.3"\n', encoding="utf-8"
        )
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("Name is 'mpdt'", output)


class TestItRefusesToGuess(ArtifactCase):
    def test_no_wheel_is_an_error_not_a_pass(self) -> None:
        write_sdist(self.dist / "mpdt-1.2.3.tar.gz")
        code, output = self.verify()
        self.assertEqual(code, 2)
        self.assertIn("build first", output)

    def test_two_wheels_are_ambiguous(self) -> None:
        self.build()
        write_wheel(self.dist / "mpdt-1.2.2-py3-none-any.whl", version="1.2.2")
        code, output = self.verify()
        self.assertEqual(code, 2)
        self.assertIn("more than one", output)

    def test_a_missing_directory_is_an_error(self) -> None:
        code, _ = self.verify()
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
