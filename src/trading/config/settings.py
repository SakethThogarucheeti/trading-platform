from __future__ import annotations

import logging
from datetime import time
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


class AlgoSettings(BaseModel):
    """
    Declarative configuration for one trading algo.

    Each algo is a self-contained trading engine with its own instruments,
    strategy, risk controller, feature engine, execution engine, and equity.

    Serialised as JSON in the ALGOS environment variable:
        ALGOS='[{"name":"momentum","instruments":["INFY","TCS"],"equity":100000}]'

    When ``algos`` is empty in ``Settings``, a single default algo is assembled
    from all instruments in the database (backward-compatible behaviour).
    """

    name: str
    instruments: list[str]  # trading symbols, must exist in instruments table
    broker_name: str = "zerodha"  # "zerodha" | "paper"
    strategy_id: str = "ema_crossover"  # registered strategy identifier
    risk_controller_id: str = "default"  # registered risk controller identifier
    execution_engine_id: str = "direct"  # registered execution engine identifier
    candle_intervals: list[str] | None = None  # None → use global Settings.candle_intervals
    equity: float = 100_000.0  # capital allocated for risk sizing


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    # Broker credentials — required, no defaults                          #
    # ------------------------------------------------------------------ #
    zerodha_api_key: str
    zerodha_api_secret: str
    zerodha_access_token: str = ""  # populated after login; empty at boot

    # ------------------------------------------------------------------ #
    # Infrastructure — required                                           #
    # ------------------------------------------------------------------ #
    postgres_url: PostgresDsn
    redis_url: str | None = None

    # ------------------------------------------------------------------ #
    # Risk controls — optional with safe defaults                         #
    # ------------------------------------------------------------------ #
    max_daily_loss_pct: float = Field(default=2.0, gt=0, le=100)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=100)

    # Market hours — using hour/minute ints so .env stays readable
    intraday_cutoff_hour: int = Field(default=15, ge=0, le=23)
    intraday_cutoff_minute: int = Field(default=30, ge=0, le=59)

    # ------------------------------------------------------------------ #
    # Engine                                                              #
    # ------------------------------------------------------------------ #
    heartbeat_interval_secs: int = Field(default=5, gt=0)
    heartbeat_timeout_secs: int = Field(default=15, gt=0)
    candle_intervals: list[str] = Field(default=["1min", "5min", "15min"])
    warmup_candles: int = Field(default=200, gt=0)
    circuit_timeout_secs: float = Field(default=30.0, gt=0)
    ws_connect_timeout_secs: float = Field(default=30.0, gt=0)
    order_timeout_secs: float = Field(default=10.0, gt=0)

    # ------------------------------------------------------------------ #
    # Paper trading — simulates orders without hitting Zerodha            #
    # ------------------------------------------------------------------ #
    paper_trading: bool = False
    paper_slippage_pct: float = Field(default=0.05, ge=0)  # % of notional per fill leg

    # ------------------------------------------------------------------ #
    # Algo configuration — one entry per trading algo                    #
    # ALGOS='[{"name":"momentum","instruments":["INFY"],"equity":10000}]'
    # When empty, a single default algo is assembled from all DB instruments
    # using default_equity as its capital.
    # ------------------------------------------------------------------ #
    algos: list[AlgoSettings] = Field(default_factory=list)  # type: ignore[assignment]
    default_equity: float = Field(default=10_000.0, gt=0)

    # ------------------------------------------------------------------ #
    # Monitoring — optional; alerter is disabled when absent              #
    # ------------------------------------------------------------------ #
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # ------------------------------------------------------------------ #
    # Dashboard                                                           #
    # ------------------------------------------------------------------ #
    dashboard_enabled: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8081
    results_dir: Path = Field(default=Path("results"))

    # ------------------------------------------------------------------ #
    # Timezone                                                            #
    # ------------------------------------------------------------------ #
    timezone: str = "Asia/Kolkata"

    # ------------------------------------------------------------------ #
    # Login callback server                                               #
    # ------------------------------------------------------------------ #
    login_callback_port: int = 8080

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # silently ignore unknown env vars
    )

    # ------------------------------------------------------------------ #
    # Computed properties                                                 #
    # ------------------------------------------------------------------ #

    @property
    def intraday_cutoff(self) -> time:
        """Market cutoff as a datetime.time for direct comparison with now().time()."""
        return time(self.intraday_cutoff_hour, self.intraday_cutoff_minute)

    @property
    def telegram_enabled(self) -> bool:
        return self.telegram_bot_token is not None and self.telegram_chat_id is not None

    # ------------------------------------------------------------------ #
    # Validators                                                          #
    # ------------------------------------------------------------------ #

    @field_validator("candle_intervals")
    @classmethod
    def candle_intervals_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("candle_intervals must contain at least one interval")
        return v

    @field_validator("heartbeat_timeout_secs")
    @classmethod
    def timeout_greater_than_interval(cls, v: int, info: object) -> int:
        # Soft guard: timeout should exceed interval so a single missed beat
        # doesn't immediately trigger an alert.
        data = getattr(info, "data", {})
        interval = data.get("heartbeat_interval_secs", 0)
        if interval and v <= interval:
            raise ValueError(
                f"heartbeat_timeout_secs ({v}) must be greater than "
                f"heartbeat_interval_secs ({interval})"
            )
        return v

    @model_validator(mode="after")
    def warn_if_live_without_access_token(self) -> Settings:
        if not self.paper_trading and not self.zerodha_access_token:
            _log.warning(
                "Settings: zerodha_access_token is empty and paper_trading=False. "
                "Live order placement will fail. Set ZERODHA_ACCESS_TOKEN before trading."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (loaded once, cached forever)."""
    return Settings()  # type: ignore[call-arg]  # args supplied via env vars
