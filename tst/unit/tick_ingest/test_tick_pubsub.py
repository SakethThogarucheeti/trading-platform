"""Unit tests for RedisCircuitBreaker and TickAgentComponent."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.core.schemas import InstrumentType, TickEvent
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tick(token: int = 738561, price: float = 1500.0) -> TickEvent:
    return TickEvent(
        instrument_token=token,
        last_price=price,
        volume=1000,
        instrument_type=InstrumentType.EQUITY,
        timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        tick_log_id=1,
    )


# ---------------------------------------------------------------------------
# RedisCircuitBreaker
# ---------------------------------------------------------------------------


class TestRedisCircuitBreaker:
    def _redis(self, value: bytes | None = None) -> MagicMock:
        r = MagicMock()
        r.get = AsyncMock(return_value=value)
        return r

    def test_inherits_circuit_breaker(self) -> None:
        from trading.tick_ingest.api import CircuitBreaker
        assert issubclass(RedisCircuitBreaker, CircuitBreaker)

    def test_initial_state_closed(self) -> None:
        cb = RedisCircuitBreaker(MagicMock())
        assert cb.is_open() is False

    @pytest.mark.anyio
    async def test_sync_loop_sets_open_when_redis_returns_open(self) -> None:
        redis = self._redis(value=b"open")
        cb = RedisCircuitBreaker(redis, poll_interval_secs=0.001)
        # Run exactly one iteration by cancelling after first sleep
        import anyio

        async def _run_once() -> None:
            with anyio.move_on_after(0.05):
                await cb.sync_loop()

        await _run_once()
        assert cb.is_open() is True

    @pytest.mark.anyio
    async def test_sync_loop_sets_closed_when_redis_returns_closed(self) -> None:
        redis = self._redis(value=b"closed")
        cb = RedisCircuitBreaker(redis, poll_interval_secs=0.001)
        import anyio

        async def _run_once() -> None:
            with anyio.move_on_after(0.05):
                await cb.sync_loop()

        await _run_once()
        assert cb.is_open() is False

    @pytest.mark.anyio
    async def test_sync_loop_keeps_last_state_on_redis_error(self) -> None:
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
        cb = RedisCircuitBreaker(redis, poll_interval_secs=0.001)
        # Force-open the circuit before the sync loop runs
        cb.open()
        import anyio

        async def _run_once() -> None:
            with anyio.move_on_after(0.05):
                await cb.sync_loop()

        await _run_once()
        # Redis error → state unchanged → still open
        assert cb.is_open() is True


# ---------------------------------------------------------------------------
# TickAgentComponent
# ---------------------------------------------------------------------------


class TestTickAgentComponent:
    def _make_agent(self, tokens=None, tick_pipeline=None, price_store=None):
        import faust

        from trading.worker.tick_agent import TickAgentComponent

        app = faust.App("test-agent", broker="kafka://localhost:19999", store="memory://")
        circuit = RedisCircuitBreaker(MagicMock())
        pipeline = tick_pipeline or MagicMock(run=AsyncMock())
        agent = TickAgentComponent(
            app=app,
            tokens=tokens or [738561],
            tick_pipeline=pipeline,
            circuit_breaker=circuit,
            token_symbol={738561: "INFY"},
            price_store=price_store,
        )
        return agent, pipeline

    def test_empty_token_set_raises(self) -> None:
        import faust

        from trading.worker.tick_agent import TickAgentComponent

        app = faust.App("test-agent-empty", broker="kafka://localhost:19999", store="memory://")
        circuit = RedisCircuitBreaker(MagicMock())
        pipeline = MagicMock(run=AsyncMock())

        with pytest.raises(ValueError, match="empty token set"):
            TickAgentComponent(
                app=app,
                tokens=[],
                tick_pipeline=pipeline,
                circuit_breaker=circuit,
                token_symbol={},
            )

    @pytest.mark.anyio
    async def test_process_ticks_runs_pipeline_for_known_token(self) -> None:
        tick = _tick(token=738561)

        async def _stream():
            yield tick.model_dump_json().encode()

        agent, pipeline = self._make_agent(tokens=[738561])
        await agent._process_ticks(_stream())

        pipeline.run.assert_awaited_once()
        (called_tick,) = pipeline.run.call_args.args
        assert called_tick.instrument_token == 738561

    @pytest.mark.anyio
    async def test_process_ticks_skips_unknown_token(self) -> None:
        tick = _tick(token=999999)

        async def _stream():
            yield tick.model_dump_json().encode()

        agent, pipeline = self._make_agent(tokens=[738561])
        await agent._process_ticks(_stream())

        pipeline.run.assert_not_awaited()

    @pytest.mark.anyio
    async def test_process_ticks_skips_invalid_json(self) -> None:
        async def _stream():
            yield b"not-valid-json"

        agent, pipeline = self._make_agent(tokens=[738561])
        await agent._process_ticks(_stream())

        pipeline.run.assert_not_awaited()

    @pytest.mark.anyio
    async def test_process_ticks_updates_price_store(self) -> None:
        from trading.broker.service.paper_broker import PriceStore

        tick = _tick(token=738561, price=1600.0)

        async def _stream():
            yield tick.model_dump_json().encode()

        price_store = PriceStore()
        agent, _ = self._make_agent(tokens=[738561], price_store=price_store)
        await agent._process_ticks(_stream())

        assert price_store.get("INFY") == pytest.approx(1600.0)

    @pytest.mark.anyio
    async def test_process_ticks_swallows_pipeline_errors(self) -> None:
        tick = _tick(token=738561)

        async def _stream():
            yield tick.model_dump_json().encode()

        bad_pipeline = MagicMock(run=AsyncMock(side_effect=RuntimeError("pipeline error")))
        agent, _ = self._make_agent(tokens=[738561], tick_pipeline=bad_pipeline)

        # Must not raise
        await agent._process_ticks(_stream())
