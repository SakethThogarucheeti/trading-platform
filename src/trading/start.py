"""
One-command startup: bring up Postgres via Docker Compose, wait for it to be
healthy, then launch the trading bot in the same process.

Usage
-----
    uv run start
"""

from __future__ import annotations

import subprocess
import sys
import time


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, **kwargs)  # type: ignore[call-overload]


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def _start_infra() -> None:
    print(">>> Starting Postgres …")
    result = _run(_compose("up", "postgres", "-d"), capture_output=True)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit("ERROR: docker compose failed — is Docker running?")


def _wait_healthy(service: str, timeout: int = 60) -> None:
    print(f">>> Waiting for {service} to be healthy …", end="", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            _compose("ps", "--format", "{{.Health}}", service),
            capture_output=True,
        )
        if result.stdout.strip() == "healthy":
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(2)
    print()
    sys.exit(f"ERROR: {service} did not become healthy within {timeout}s")


def main() -> None:
    _start_infra()
    _wait_healthy("postgres")

    print(">>> Launching trading bot …\n")
    # Replace the current process with the bot so Ctrl+C propagates naturally.
    import os

    os.execv(sys.executable, [sys.executable, "main.py"])


if __name__ == "__main__":
    main()
