#!/usr/bin/env python3
"""Generate a report from the test fixtures and write it to a directory.

Used to prove a refactor of `mpdt/report.py` changes no output: run it before,
run it after, and diff the two directories. Assertions in the test suite cover
the parts the authors thought to check; a byte diff covers everything.

    python tools/report_snapshot.py /tmp/before
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_report import ReportBase  # noqa: E402

from mpdt import report  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    dest = Path(sys.argv[1])
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for week in ("2026-W42", "2026-W41"):
        case = ReportBase("run")
        case.setUp()
        try:
            with tempfile.TemporaryDirectory() as out_dir:
                result = report.generate(case.con, week, out_dir)
                for key in ("md", "html"):
                    src = Path(result[key])
                    shutil.copyfile(src, dest / f"{week}-{key}{src.suffix}")
                (dest / f"{week}-rows.txt").write_text(f"rows={result['rows']}\n", encoding="utf-8")
        finally:
            case.tearDown()
    print(f"wrote {sorted(p.name for p in dest.iterdir())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
