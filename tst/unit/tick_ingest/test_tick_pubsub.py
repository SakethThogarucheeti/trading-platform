"""Unit tests for TickPublisher, TickSubscriber, and RedisCircuitBreaker."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.core.schemas import InstrumentType, TickEvent
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker
from trading.tick_ingest.tick_publisher import TickPublisher


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
# TickPublisher
# ---------------------------------------------------------------------------


class TestTickPublisher:
    def _redis(self) -> MagicMock:
        r = MagicMock()
        r.publish = AsyncMock()
        r.set = AsyncMock()
        return r

    @pytest.mark.anyio
    async def test_publish_sends_to_correct_channel(self) -> None:
        redis = self._redis()
        pub = TickPublisher(redis)
        tick = _tick(token=738561)
        await pub.publish(tick)
        redis.publish.assert_awaited_once()
        channel, payload = redis.publish.call_args.args
        assert channel == "ticks:738561"
        assert "738561" in payload  # JSON payload contains the token

    @pytest.mark.anyio
    async def test_publish_payload_is_valid_tick_json(self) -> None:
        redis = self._redis()
        pub = TickPublisher(redis)
        tick = _tick(token=111111, price=2000.0)
        await pub.publish(tick)
        _, payload = redis.publish.call_args.args
        restored = TickEvent.model_validate_json(payload)
        assert restored.instrument_token == 111111
        assert restored.last_price == pytest.approx(2000.0)

    @pytest.mark.anyio
    async def test_publish_swallows_redis_errors(self) -> None:
        redis = MagicMock()
        redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
        pub = TickPublisher(redis)
        # Must not raise
        await pub.publish(_tick())

    @pytest.mark.anyio
    async def test_set_circuit_state_open(self) -> None:
        redis = self._redis()
        pub = TickPublisher(redis)
        await pub.set_circuit_state(open=True)
        redis.set.assert_awaited_once_with("circuit:state", "open")

    @pytest.mark.anyio
    async def test_set_circuit_state_closed(self) -> None:
        redis = self._redis()
        pub = TickPublisher(redis)
        await pub.set_circuit_state(open=False)
        redis.set.assert_awaited_once_with("circuit:state", "closed")

    @pytest.mark.anyio
    async def test_set_circuit_state_swallows_errors(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
        pub = TickPublisher(redis)
        await pub.set_circuit_state(open=True)  # must not raise


# ---------------------------------------------------------------------------
# RedisCircuitBreaker
# ---------------------------------------------------------------------------


class TestRedisCircuitBreaker:
    def _redis(self, value: bytes | None = None) -> MagicMock:
        r = MagicMock()
        r.get = AsyncMock(return_value=value)
        return r

    def test_inherits_circuit_breaker(self) -> None:
        from trading.tick_ingest.tick_ingestor import CircuitBreaker
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
# TickSubscriber
# ---------------------------------------------------------------------------


class TestTickSubscriber:
    def _make_subscriber(self, tokens=None, callbacks=None):
        from trading.worker.tick_subscriber import TickSubscriber

        redis = MagicMock()
        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()
        redis.pubsub = MagicMock(return_value=pubsub)

        circuit = RedisCircuitBreaker(MagicMock())
        sub = TickSubscriber(
            redis=redis,
            tokens=tokens or [738561],
            circuit_breaker=circuit,
            token_symbol={738561: "INFY"},
        )
        for cb in (callbacks or []):
            sub.add_on_tick(cb)
        return sub, pubsub

    @pytest.mark.anyio
    async def test_setup_subscribes_to_channels(self) -> None:
        sub, pubsub = self._make_subscriber(tokens=[738561, 260105])
        await sub._setup()
        pubsub.subscribe.assert_awaited_once_with("ticks:738561", "ticks:260105")

    @pytest.mark.anyio
    async def test_teardown_unsubscribes_and_closes(self) -> None:
        sub, pubsub = self._make_subscriber()
        await sub._setup()
        await sub._teardown()
        pubsub.unsubscribe.assert_awaited()
        pubsub.aclose.assert_awaited()

    @pytest.mark.anyio
    async def test_teardown_is_idempotent_when_not_setup(self) -> None:
        sub, _ = self._make_subscriber()
        await sub._teardown()  # should not raise

    @pytest.mark.anyio
    async def test_listen_dispatches_tick_to_callbacks(self) -> None:
        tick = _tick()
        messages = [
            {"type": "message", "data": tick.model_dump_json().encode()},
        ]

        received: list[TickEvent] = []

        async def _on_tick(t: TickEvent) -> None:
            received.append(t)

        sub, pubsub = self._make_subscriber(callbacks=[_on_tick])

        # Make pubsub.listen() return our messages async-iterably
        async def _listen():
            for m in messages:
                yield m

        pubsub.listen = _listen
        sub._pubsub = pubsub

        await sub._listen()

        assert len(received) == 1
        assert received[0].instrument_token == tick.instrument_token

    @pytest.mark.anyio
    async def test_listen_skips_non_message_type(self) -> None:
        messages = [{"type": "subscribe", "data": b""}]
        received: list[TickEvent] = []

        async def _on_tick(t: TickEvent) -> None:
            received.append(t)

        sub, pubsub = self._make_subscriber(callbacks=[_on_tick])

        async def _listen():
            for m in messages:
                yield m

        pubsub.listen = _listen
        sub._pubsub = pubsub

        await sub._listen()
        assert received == []

    @pytest.mark.anyio
    async def test_listen_skips_invalid_json(self) -> None:
        messages = [{"type": "message", "data": b"not-valid-json"}]
        received: list[TickEvent] = []

        async def _on_tick(t: TickEvent) -> None:
            received.append(t)

        sub, pubsub = self._make_subscriber(callbacks=[_on_tick])

        async def _listen():
            for m in messages:
                yield m

        pubsub.listen = _listen
        sub._pubsub = pubsub

        await sub._listen()
        assert received == []

    @pytest.mark.anyio
    async def test_listen_updates_price_store(self) -> None:
        from trading.broker.paper_broker import PriceStore

        tick = _tick(token=738561, price=1600.0)
        messages = [{"type": "message", "data": tick.model_dump_json().encode()}]

        from trading.worker.tick_subscriber import TickSubscriber

        redis = MagicMock()
        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        redis.pubsub = MagicMock(return_value=pubsub)

        price_store = PriceStore()
        circuit = RedisCircuitBreaker(MagicMock())
        sub = TickSubscriber(
            redis=redis,
            tokens=[738561],
            circuit_breaker=circuit,
            token_symbol={738561: "INFY"},
            price_store=price_store,
        )

        async def _listen():
            for m in messages:
                yield m

        pubsub.listen = _listen
        sub._pubsub = pubsub

        await sub._listen()
        assert price_store.get("INFY") == pytest.approx(1600.0)

    @pytest.mark.anyio
    async def test_listen_swallows_callback_errors(self) -> None:
        tick = _tick()
        messages = [{"type": "message", "data": tick.model_dump_json().encode()}]

        async def _bad_callback(t: TickEvent) -> None:
            raise RuntimeError("callback error")

        sub, pubsub = self._make_subscriber(callbacks=[_bad_callback])

        async def _listen():
            for m in messages:
                yield m

        pubsub.listen = _listen
        sub._pubsub = pubsub

        # Must not raise
        await sub._listen()
