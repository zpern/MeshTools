"""Run all source regression tests without retaining test caches."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mesh-tools-tests-") as temporary:
        return subprocess.call(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                "-m",
                "pytest",
                "tests",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                temporary,
            ],
            cwd=ROOT,
        )


if __name__ == "__main__":
    raise SystemExit(main())
