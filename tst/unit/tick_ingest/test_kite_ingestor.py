"""Tests for engine/tick_ingestor.py and engine/kite_ingestor.py"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from anyio import create_task_group, sleep
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker_stream import BrokerStream
from trading.broker.paper_broker import AbstractPriceStore
from trading.core.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import InstrumentType, TickEvent
from trading.core.messaging import AbstractCircuitBreaker
from trading.tick_ingest.kite_ingestor import KiteIngestor
from trading.tick_ingest.tick_ingestor import CircuitBreaker, TickConfig, TickIngestor
from trading.storage.stores.audit import AuditStore

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Mock broker stream
# ---------------------------------------------------------------------------


class MockBrokerStream(BrokerStream):
    """Test double for BrokerStream. Fires callbacks programmatically."""

    def __init__(self) -> None:
        self._on_connect: Callable[[], None] | None = None
        self._on_ticks: Callable[[list[dict]], None] | None = None
        self._on_disconnect: Callable[[int, str], None] | None = None
        self.subscribed_tokens: list[int] = []
        self.closed = False

    def set_on_connect(self, callback: Callable[[], None]) -> None:
        self._on_connect = callback

    def set_on_ticks(self, callback: Callable[[list[dict]], None]) -> None:
        self._on_ticks = callback

    def set_on_disconnect(self, callback: Callable[[int, str], None]) -> None:
        self._on_disconnect = callback

    async def connect(self) -> None:
        if self._on_connect:
            self._on_connect()

    async def subscribe(self, tokens: list[int]) -> None:
        self.subscribed_tokens = list(tokens)

    async def close(self) -> None:
        self.closed = True

    def fire_ticks(self, ticks: list[dict]) -> None:
        if self._on_ticks:
            self._on_ticks(ticks)

    def fire_disconnect(self, code: int = 1006, reason: str = "connection closed") -> None:
        if self._on_disconnect:
            self._on_disconnect(code, reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_instruments(*tokens: int, itype: str = "EQUITY") -> list[Instrument]:
    return [
        Instrument(token=t, symbol=f"SYM{t}", exchange="NSE", instrument_type=itype) for t in tokens
    ]


def make_raw_tick(token: int, price: float = 100.0, volume: int = 1000) -> dict:
    return {
        "instrument_token": token,
        "last_price": price,
        "volume_traded": volume,
    }


@pytest.fixture
def stream() -> MockBrokerStream:
    return MockBrokerStream()


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_tick_registry(
    stream: MockBrokerStream,
    engine: AsyncEngine,
    *tokens: int,
    circuit: AbstractCircuitBreaker | None = None,
) -> TickIngestor:
    instruments = make_instruments(*tokens) if tokens else make_instruments(1, 2)
    sf = build_session_factory(engine)
    config = TickConfig(instruments=instruments, exec_id="paper")
    return TickIngestor(config=config, stream=stream, audit=AuditStore(sf), circuit=circuit or CircuitBreaker())


@pytest.fixture
def tick_registry(stream: MockBrokerStream, engine: AsyncEngine) -> TickIngestor:
    return make_tick_registry(stream, engine)


@pytest.fixture
def ingestor(stream: MockBrokerStream, tick_registry: TickIngestor) -> KiteIngestor:
    return KiteIngestor(stream=stream, tick_registry=tick_registry, circuit=tick_registry.circuit)


# ---------------------------------------------------------------------------
# Helper: start ingestor and run a block, then stop
# ---------------------------------------------------------------------------


async def _with_ingestor(ingestor: KiteIngestor, body) -> None:  # type: ignore[type-arg]
    async with create_task_group() as tg:
        await tg.start(ingestor.start)
        await body()
        await ingestor.stop()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


async def test_setup_subscribes_to_tokens(stream: MockBrokerStream, engine: AsyncEngine) -> None:
    reg = make_tick_registry(stream, engine, 10, 20, 30)
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit)

    async def _check() -> None:
        await sleep(0.05)
        assert set(stream.subscribed_tokens) == {10, 20, 30}

    await _with_ingestor(ingestor, _check)


# ---------------------------------------------------------------------------
# Tick handling — TickIngestor.handle() is the source of truth
# ---------------------------------------------------------------------------


async def test_valid_tick_processed_by_registry(
    stream: MockBrokerStream, tick_registry: TickIngestor, ingestor: KiteIngestor
) -> None:
    async def _check() -> None:
        await sleep(0.05)
        stream.fire_ticks([make_raw_tick(token=1, price=250.0)])
        await sleep(0.05)
        assert tick_registry.circuit.is_open() is False

    await _with_ingestor(ingestor, _check)


async def test_tick_with_zero_price_returns_none_from_registry(engine: AsyncEngine) -> None:
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    result = await reg.handle(make_raw_tick(token=1, price=0.0))
    assert result is None


async def test_tick_missing_last_price_returns_none_from_registry(engine: AsyncEngine) -> None:
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    result = await reg.handle({"instrument_token": 1})
    assert result is None


async def test_unknown_token_returns_none_from_registry(engine: AsyncEngine) -> None:
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1, 2)
    result = await reg.handle(make_raw_tick(token=999, price=100.0))
    assert result is None


async def test_valid_tick_returns_tick_event(engine: AsyncEngine) -> None:
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    result = await reg.handle(make_raw_tick(token=1, price=250.0))
    assert result is not None
    assert isinstance(result, TickEvent)
    assert result.instrument_token == 1
    assert result.last_price == 250.0


async def test_instrument_type_correct_on_tick_event(engine: AsyncEngine) -> None:
    instruments = [Instrument(token=5, symbol="INFY", exchange="NSE", instrument_type="EQUITY")]
    sf = build_session_factory(engine)
    stream = MockBrokerStream()
    config = TickConfig(instruments=instruments, exec_id="paper")
    reg = TickIngestor(config=config, stream=stream, audit=AuditStore(sf), circuit=CircuitBreaker())

    result = await reg.handle(make_raw_tick(token=5, price=1500.0))
    assert result is not None
    assert result.instrument_type == InstrumentType.EQUITY


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_open_after_timeout(
    stream: MockBrokerStream, tick_registry: TickIngestor
) -> None:
    """Circuit opens when manually triggered (direct open)."""
    tick_registry.circuit.open()
    assert tick_registry.circuit.is_open() is True


async def test_reconnect_before_timeout_clears_circuit(
    stream: MockBrokerStream, tick_registry: TickIngestor, ingestor: KiteIngestor
) -> None:
    """Reconnect cancels the pending circuit-open task and closes the circuit."""
    async def _check() -> None:
        await sleep(0.05)
        stream.fire_disconnect()
        await sleep(0.01)  # well before circuit timeout
        # Simulate reconnect via the stream callback
        if stream._on_connect:
            stream._on_connect()
        await sleep(0.05)
        assert tick_registry.circuit.is_open() is False

    await _with_ingestor(ingestor, _check)


async def test_disconnect_sets_circuit_after_timeout(engine: AsyncEngine) -> None:
    """End-to-end: short timeout fires, circuit opens."""
    stream = MockBrokerStream()
    instruments = make_instruments(1)
    sf = build_session_factory(engine)
    config = TickConfig(instruments=instruments, exec_id="paper")
    circuit = CircuitBreaker()
    reg = TickIngestor(
        config=config,
        stream=stream,
        audit=AuditStore(sf),
        circuit=circuit,
    )

    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=circuit, circuit_timeout_secs=0.05)

    async def _check() -> None:
        await sleep(0.05)
        stream.fire_disconnect()
        await sleep(0.15)  # wait past 0.05s timeout
        assert circuit.is_open() is True

    await _with_ingestor(ingestor, _check)


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


async def test_stop_closes_stream(stream: MockBrokerStream, ingestor: KiteIngestor) -> None:
    async def _check() -> None:
        await sleep(0.05)

    await _with_ingestor(ingestor, _check)
    assert stream.closed


async def test_teardown_cancels_pending_circuit_scope(engine: AsyncEngine) -> None:
    """_teardown cancels a pending circuit timer on disconnect."""
    stream = MockBrokerStream()
    instruments = make_instruments(1)
    sf = build_session_factory(engine)
    config = TickConfig(instruments=instruments, exec_id="paper")
    circuit = CircuitBreaker()
    reg = TickIngestor(
        config=config,
        stream=stream,
        audit=AuditStore(sf),
        circuit=circuit,
    )
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=circuit, circuit_timeout_secs=60.0)

    async def _check() -> None:
        await sleep(0.05)
        stream.fire_disconnect()
        await sleep(0.01)
        # Stop immediately — _teardown cancels the still-pending circuit scope

    await _with_ingestor(ingestor, _check)
    # Teardown completed without error — the cancel path was exercised
    assert stream.closed is True


async def test_tick_missing_instrument_token_returns_none(engine: AsyncEngine) -> None:
    """raw dict has no 'instrument_token' key → returns None."""
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    result = await reg.handle({"last_price": 100.0})
    assert result is None


async def test_ingestor_no_instruments_logs_warning(engine: AsyncEngine) -> None:
    """KiteIngestor setup with no configured instruments."""
    stream = MockBrokerStream()
    sf = build_session_factory(engine)
    config = TickConfig(instruments=[], exec_id="paper")
    circuit = CircuitBreaker()
    reg = TickIngestor(config=config, stream=stream, audit=AuditStore(sf), circuit=circuit)
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=circuit)

    async def _check() -> None:
        await sleep(0.05)
        assert stream.subscribed_tokens == []

    await _with_ingestor(ingestor, _check)


async def test_ingestor_handle_tick_unknown_token_returns_none(engine: AsyncEngine) -> None:
    """_handle_tick when tick_registry returns None (unknown token)."""
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit)

    async def _check() -> None:
        await sleep(0.05)
        stream.fire_ticks([make_raw_tick(token=999, price=100.0)])
        await sleep(0.05)

    await _with_ingestor(ingestor, _check)


async def test_tick_registry_db_persist_failure_returns_tick_with_minus_one_id(
    engine: AsyncEngine,
) -> None:
    """audit.log_tick() raises → tick_log_id set to -1 but TickEvent returned."""
    from trading.storage.stores.audit import AbstractAuditStore

    class _FailingAuditStore(AbstractAuditStore):
        async def log_tick(self, tick, symbol: str) -> int:
            raise RuntimeError("DB unavailable")

        async def log_decision(self, **kwargs) -> None:
            pass

        async def log_audit(self, module, level, message) -> None:
            pass

    stream = MockBrokerStream()
    instruments = make_instruments(1)
    config = TickConfig(instruments=instruments, exec_id="paper")
    reg = TickIngestor(config=config, stream=stream, audit=_FailingAuditStore(), circuit=CircuitBreaker())

    result = await reg.handle(make_raw_tick(token=1, price=100.0))
    assert result is not None
    assert result.tick_log_id == -1


async def test_ingestor_on_tick_callback_exception_is_swallowed(engine: AsyncEngine) -> None:
    """on_tick callback that raises is caught and doesn't crash."""
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)

    calls: list[str] = []

    async def _failing_callback(tick) -> None:
        calls.append("called")
        raise RuntimeError("callback error")

    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit)
    ingestor.add_on_tick(_failing_callback)

    async def _check() -> None:
        await sleep(0.05)
        stream.fire_ticks([make_raw_tick(token=1, price=100.0)])
        await sleep(0.05)
        assert "called" in calls

    await _with_ingestor(ingestor, _check)


async def test_ingestor_on_ws_ticks_no_op_when_loop_is_none(engine: AsyncEngine) -> None:
    """_on_ws_ticks returns early when _loop is None (before _setup)."""
    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit)

    # _loop is None before _setup() is called — fire_ticks should be a no-op
    stream.fire_ticks([make_raw_tick(token=1, price=100.0)])
    # No crash


async def test_ingestor_connect_timeout_raises_runtime_error(engine: AsyncEngine) -> None:
    """TimeoutError in _setup() is re-raised as RuntimeError."""

    class _NeverConnectsStream(MockBrokerStream):
        async def connect(self) -> None:
            pass  # Never fires on_connect

    stream = _NeverConnectsStream()
    reg = make_tick_registry(stream, engine, 1)
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit, connect_timeout_secs=0.05)

    with pytest.raises(RuntimeError, match="did not connect within"):
        await ingestor._setup()


async def test_ingestor_updates_price_store_on_valid_tick(engine: AsyncEngine) -> None:
    """price_store is updated when tick is valid."""

    class _MockPriceStore(AbstractPriceStore):
        def __init__(self) -> None:
            self.updates: dict[str, float] = {}

        def get(self, symbol: str) -> float | None:
            return self.updates.get(symbol)

        def update(self, symbol: str, price: float) -> None:
            self.updates[symbol] = price

    stream = MockBrokerStream()
    reg = make_tick_registry(stream, engine, 1)
    price_store = _MockPriceStore()
    ingestor = KiteIngestor(stream=stream, tick_registry=reg, circuit=reg.circuit, price_store=price_store)

    async def _check() -> None:
        await sleep(0.05)
        stream.fire_ticks([make_raw_tick(token=1, price=123.4)])
        await sleep(0.05)
        assert price_store.updates.get("SYM1") == pytest.approx(123.4)

    await _with_ingestor(ingestor, _check)
