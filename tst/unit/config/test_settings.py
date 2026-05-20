"""Tests for config/settings.py"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trading.config.settings import Settings, get_settings

# ---------------------------------------------------------------------------
# Minimal valid kwargs — reused across tests
# ---------------------------------------------------------------------------

VALID = dict(
    zerodha_api_key="key123",
    zerodha_api_secret="secret123",
    postgres_url="postgresql+asyncpg://user:pass@localhost:5432/trading",
)


def make(**overrides: object) -> Settings:
    return Settings(**{**VALID, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_valid_settings_instantiates() -> None:
    s = make()
    assert s.zerodha_api_key == "key123"


def test_defaults_applied() -> None:
    # Bypass .env so we see the true code defaults, not developer overrides.
    s = Settings(**{**VALID, "_env_file": None})  # type: ignore[arg-type]
    assert s.max_daily_loss_pct == 2.0
    assert s.risk_per_trade_pct == 1.0
    assert s.candle_intervals == ["1min", "5min", "15min"]
    assert s.warmup_candles == 200
    assert s.heartbeat_interval_secs == 5
    assert s.heartbeat_timeout_secs == 15


def test_intraday_cutoff_property() -> None:
    from datetime import time

    s = make(intraday_cutoff_hour=15, intraday_cutoff_minute=30)
    assert s.intraday_cutoff == time(15, 30)


def test_telegram_disabled_by_default() -> None:
    s = make()
    assert s.telegram_bot_token is None
    assert s.telegram_chat_id is None
    assert s.telegram_enabled is False


def test_telegram_enabled_when_both_fields_set() -> None:
    s = make(telegram_bot_token="tok", telegram_chat_id="123")
    assert s.telegram_enabled is True


def test_telegram_enabled_false_when_only_token_set() -> None:
    s = make(telegram_bot_token="tok")
    assert s.telegram_enabled is False


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset env vars so pydantic-settings cannot fall back to the real .env
    monkeypatch.delenv("ZERODHA_API_KEY", raising=False)

    class _NoEnvSettings(Settings):
        model_config = Settings.model_config.copy()  # type: ignore[attr-defined]

        @classmethod
        def settings_customise_sources(cls, settings_cls, **kwargs):  # type: ignore[override]
            # Drop env-file source so the real .env is never read
            init_kwargs = kwargs.get("init_settings")
            env_vars = kwargs.get("env_settings")
            return (init_kwargs, env_vars) if init_kwargs and env_vars else (init_kwargs,)

    with pytest.raises(ValidationError) as exc:
        _NoEnvSettings(  # type: ignore[call-arg]
            zerodha_api_secret="s",
            postgres_url=VALID["postgres_url"],
        )
    assert "zerodha_api_key" in str(exc.value)


def test_missing_postgres_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(  # type: ignore[call-arg]
            zerodha_api_key="k",
            zerodha_api_secret="s",
            _env_file=None,
        )
    assert "postgres_url" in str(exc.value)


# ---------------------------------------------------------------------------
# Constraint validation
# ---------------------------------------------------------------------------


def test_max_daily_loss_pct_zero_raises() -> None:
    with pytest.raises(ValidationError):
        make(max_daily_loss_pct=0.0)


def test_max_daily_loss_pct_negative_raises() -> None:
    with pytest.raises(ValidationError):
        make(max_daily_loss_pct=-1.0)


def test_max_daily_loss_pct_over_100_raises() -> None:
    with pytest.raises(ValidationError):
        make(max_daily_loss_pct=101.0)


def test_risk_per_trade_pct_zero_raises() -> None:
    with pytest.raises(ValidationError):
        make(risk_per_trade_pct=0.0)


def test_warmup_candles_zero_raises() -> None:
    with pytest.raises(ValidationError):
        make(warmup_candles=0)


def test_heartbeat_interval_zero_raises() -> None:
    with pytest.raises(ValidationError):
        make(heartbeat_interval_secs=0)


def test_heartbeat_timeout_must_exceed_interval() -> None:
    with pytest.raises(ValidationError):
        make(heartbeat_interval_secs=10, heartbeat_timeout_secs=5)


def test_heartbeat_timeout_equal_to_interval_raises() -> None:
    with pytest.raises(ValidationError):
        make(heartbeat_interval_secs=10, heartbeat_timeout_secs=10)


def test_candle_intervals_empty_raises() -> None:
    with pytest.raises(ValidationError):
        make(candle_intervals=[])


def test_invalid_postgres_url_raises() -> None:
    with pytest.raises(ValidationError):
        make(postgres_url="not-a-url")


# ---------------------------------------------------------------------------
# Startup config warnings
# ---------------------------------------------------------------------------


def _capture_settings_warnings(**kwargs: object) -> list[str]:
    """Instantiate Settings and return any WARNING messages from trading.config.settings."""
    import logging

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    log = logging.getLogger("trading.config.settings")
    log.addHandler(handler)
    try:
        make(**kwargs)
    finally:
        log.removeHandler(handler)

    return [r.getMessage() for r in records if r.levelno >= logging.WARNING]


def test_empty_access_token_live_mode_emits_warning() -> None:
    """Instantiating Settings with paper_trading=False and no access token must log a warning."""
    msgs = _capture_settings_warnings(paper_trading=False, zerodha_access_token="")
    assert any("access_token" in m.lower() for m in msgs), (
        "Expected a WARNING about empty zerodha_access_token in live mode"
    )


def test_non_empty_access_token_live_mode_no_warning() -> None:
    """A set access token in live mode must not emit the warning."""
    msgs = _capture_settings_warnings(paper_trading=False, zerodha_access_token="valid_token_abc")
    assert not any("access_token" in m.lower() for m in msgs)


def test_paper_trading_mode_no_warning_on_empty_token() -> None:
    """paper_trading=True must not warn even with an empty access token."""
    msgs = _capture_settings_warnings(paper_trading=True, zerodha_access_token="")
    assert not any("access_token" in m.lower() for m in msgs)


# ---------------------------------------------------------------------------
# get_settings cache
# ---------------------------------------------------------------------------


def test_get_settings_returns_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear the lru_cache so the test is hermetic
    get_settings.cache_clear()

    # Patch env so Settings() can instantiate without a real .env
    monkeypatch.setenv("ZERODHA_API_KEY", "k")
    monkeypatch.setenv("ZERODHA_API_SECRET", "s")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")

    first = get_settings()
    second = get_settings()
    assert first is second

    get_settings.cache_clear()
