"""
Regression coverage for trading.start -- the `uv run start` entrypoint.

docker-compose.yml only defines postgres/platform/dashboard-ui services (redis
was removed in the Aug 28 Kafka/Redis migration, #59/#60); start.py's infra
bring-up and health-wait steps must never reference "redis" again, or
`docker compose up` fails outright with "no such service: redis" (#71).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading import start


def test_start_infra_never_requests_a_redis_service(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(start, "_run", fake_run)

    start._start_infra()

    assert len(calls) == 1
    cmd = calls[0]
    assert "redis" not in cmd
    assert cmd == ["docker", "compose", "up", "postgres", "-d"]


def test_start_infra_exits_on_compose_failure(monkeypatch):
    monkeypatch.setattr(
        start, "_run", lambda cmd, **kw: MagicMock(returncode=1, stderr="boom")
    )

    with pytest.raises(SystemExit):
        start._start_infra()


def test_main_only_waits_on_postgres_not_redis(monkeypatch):
    monkeypatch.setattr(start, "_start_infra", MagicMock())
    wait_healthy = MagicMock()
    monkeypatch.setattr(start, "_wait_healthy", wait_healthy)
    # main() does `import os` locally, so patch the real os module's execv.
    monkeypatch.setattr("os.execv", MagicMock())

    start.main()

    wait_healthy.assert_called_once_with("postgres")
