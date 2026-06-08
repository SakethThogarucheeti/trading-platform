from __future__ import annotations

from dishka import Provider, Scope  # type: ignore[import-untyped]


class ReportsProvider(Provider):
    """Reports module DI — read-only queries use stores injected by trading.app."""

    scope = Scope.APP
