"""
Build script: lint (ruff format + check --fix) then run all unit tests.

Usage
-----
    uv run build          (registered in pyproject.toml [project.scripts])
"""

from __future__ import annotations

import subprocess
import sys


def _run(label: str, cmd: list[str]) -> None:
    print(f"\n>>> {label}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n[FAILED] {label}")
        sys.exit(result.returncode)


def main() -> None:
    # 1. Auto-format
    _run("ruff format", [sys.executable, "-m", "ruff", "format", "src", "tst"])

    # 2. Lint with auto-fix
    _run("ruff check --fix", [sys.executable, "-m", "ruff", "check", "--fix", "src", "tst"])

    # 3. Unit tests
    _run("pytest", [sys.executable, "-m", "pytest"])

    print("\n[OK] build passed")


if __name__ == "__main__":
    main()
