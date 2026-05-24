from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.zerodha.kite_client import KiteClient
from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.models import AlgoConfig, Candle, DecisionLog, Heartbeat, Order, Position, Signal
from trading.execution.order_executor import OrderExecutor
from trading.storage.cache import CacherFactory
from trading.tick_ingest.kite_ingestor import KiteIngestor

logger = logging.getLogger(__name__)


class _CallbackRequest(BaseModel):
    request_token: str


def build_app(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock = SYSTEM_CLOCK,
    results_dir: Path | None = None,
    candle_intervals: list[str] | None = None,
    zerodha_api_key: str = "",
    zerodha_api_secret: str = "",
    token_secret_key: str = "",
    kite_client: KiteClient | None = None,
    kite_ingestor: KiteIngestor | None = None,
    order_executor: OrderExecutor | None = None,
    cacher_factory: CacherFactory | None = None,
) -> FastAPI:
    """
    Build the monitoring dashboard FastAPI application.

    All endpoints are read-only — the dashboard never writes to the DB.
    The ``session_factory`` is the same singleton used by all other components.

    Session filtering
    -----------------
    All decision-log endpoints accept an optional ``session_id`` query param:
    - Omitted / empty → live trading view (``session_id IS NULL`` in DB)
    - Named string    → backtest / Monte Carlo session
    """
    app = FastAPI(title="Algo Trading Dashboard", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    _results_dir = results_dir or Path("results")
    _candle_intervals = candle_intervals or ["5min"]

    def _today_start() -> datetime:
        now = clock.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ------------------------------------------------------------------
    # GET /api/ping — lightweight liveness probe
    # ------------------------------------------------------------------

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    # ------------------------------------------------------------------
    # GET /api/auth/login-url — return Zerodha OAuth URL
    # ------------------------------------------------------------------

    @app.get("/api/auth/login-url")
    async def get_login_url() -> JSONResponse:
        if not zerodha_api_key:
            raise HTTPException(status_code=503, detail="ZERODHA_API_KEY not configured")
        url = KiteClient(zerodha_api_key).login_url()
        return JSONResponse(content={"url": url})

    # ------------------------------------------------------------------
    # POST /api/auth/callback — exchange request_token for access_token
    # ------------------------------------------------------------------

    @app.post("/api/auth/callback")
    async def auth_callback(body: _CallbackRequest) -> JSONResponse:
        if not zerodha_api_key or not zerodha_api_secret:
            raise HTTPException(status_code=503, detail="Zerodha credentials not configured")
        if not token_secret_key:
            raise HTTPException(status_code=503, detail="TOKEN_SECRET_KEY not configured")

        # Use a temporary client just for the session exchange
        exchange_client = KiteClient(zerodha_api_key)
        try:
            session = exchange_client.generate_session(body.request_token, zerodha_api_secret)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}") from exc

        access_token: str = session["access_token"]

        # Persist the encrypted token in Postgres
        from trading.storage.stores.trading import TradingStore
        trading_store = TradingStore(session_factory)
        await trading_store.save_broker_token("zerodha", access_token, token_secret_key)

        # Update the live broker's KiteClient so in-flight trades use the new token
        if kite_client is not None:
            kite_client.set_access_token(access_token)

        # Reconnect the WebSocket stream with the new token
        if kite_ingestor is not None:
            import asyncio
            asyncio.get_event_loop().create_task(kite_ingestor.reconnect_stream())

        return JSONResponse(
            content={
                "ok": True,
                "user_name": session.get("user_name", ""),
                "login_time": str(session.get("login_time", "")),
            }
        )

    # ------------------------------------------------------------------
    # GET /api/sessions — list all distinct session_ids (for session selector)
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def get_sessions() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(DecisionLog.session_id).distinct().order_by(DecisionLog.session_id)
            )
            rows = result.fetchall()

        sessions = [r[0] for r in rows]  # may include None (live trading)
        return JSONResponse(content=sessions)

    # ------------------------------------------------------------------
    # GET /api/settings — runtime configuration (candle_intervals, etc.)
    # ------------------------------------------------------------------

    @app.get("/api/settings")
    async def get_settings_endpoint() -> JSONResponse:
        return JSONResponse(content={"candle_intervals": _candle_intervals})

    # ------------------------------------------------------------------
    # GET /api/positions — current open positions (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/positions")
    async def get_positions() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(Position)
                .where(Position.updated_at >= _today_start())
                .order_by(Position.symbol)
            )
            positions = result.scalars().all()

        return JSONResponse(
            content=[
                {
                    "symbol": p.symbol,
                    "instrument_type": p.instrument_type,
                    "net_qty": p.net_qty,
                    "avg_price": float(p.avg_price),
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in positions
            ]
        )

    # ------------------------------------------------------------------
    # GET /api/health — heartbeat status (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/health")
    async def get_health() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(select(Heartbeat).order_by(Heartbeat.module))
            heartbeats = result.scalars().all()

        now = clock.now()
        stale_threshold = 30

        rows: list[dict[str, object]] = []
        for hb in heartbeats:
            last_seen = hb.last_seen
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            stale = (now - last_seen).total_seconds() > stale_threshold
            rows.append(
                {
                    "module": hb.module,
                    "last_seen": last_seen.isoformat(),
                    "stale": stale,
                }
            )
        return JSONResponse(content=rows)

    # ------------------------------------------------------------------
    # GET /api/signals?session_id= — last 50 signals (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/signals")
    async def get_signals(session_id: str = "", algo_name: str = "") -> JSONResponse:
        async with session_factory() as session:
            conditions = [
                DecisionLog.step.in_(["SIGNAL_GENERATED", "SIGNAL_ACCEPTED", "SIGNAL_REJECTED"]),
                DecisionLog.created_at >= _today_start(),
                _session_filter(DecisionLog, session_id),
            ]
            if algo_name:
                conditions.append(DecisionLog.algo_name == algo_name)
            stmt = (
                select(DecisionLog)
                .where(*conditions)
                .order_by(DecisionLog.created_at.desc())
                .limit(50)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return JSONResponse(
            content=[
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "symbol": r.symbol,
                    "algo_name": r.algo_name or "—",
                    "step": r.step,
                    "context": r.context,
                }
                for r in rows
            ]
        )

    # ------------------------------------------------------------------
    # GET /api/algos — algo config + live state (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/algos")
    async def get_algos() -> JSONResponse:
        from trading.storage.stores.config import ConfigStore

        algos = await ConfigStore(session_factory).get_algo_configs_with_state()
        return JSONResponse(content=algos)

    # ------------------------------------------------------------------
    # GET /api/candles?symbol=&interval=&limit= — OHLCV bars (Chart.js JSON)
    # ------------------------------------------------------------------

    @app.get("/api/candles")
    async def get_candles_endpoint(
        symbol: str = "INFY",
        interval: str = "15min",
        limit: int = 100,
    ) -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.interval == interval,
                    Candle.ts >= _today_start(),
                )
                .order_by(Candle.ts.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))

        points = [
            {
                "ts": c.ts.isoformat(),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": c.volume,
            }
            for c in rows
        ]
        return JSONResponse(content=points)

    # ------------------------------------------------------------------
    # GET /api/ticks?symbol=&limit= — recent tick prices (Chart.js JSON)
    # ------------------------------------------------------------------

    @app.get("/api/ticks")
    async def get_ticks(symbol: str = "INFY", limit: int = 500) -> JSONResponse:
        async with session_factory() as session:
            from trading.core.models import TickLog

            result = await session.execute(
                select(TickLog)
                .where(
                    TickLog.symbol == symbol,
                    TickLog.received_at >= _today_start(),
                )
                .order_by(TickLog.received_at.desc())
                .limit(limit)
            )
            ticks = list(reversed(result.scalars().all()))

        points = [{"ts": t.received_at.isoformat(), "price": float(t.last_price)} for t in ticks]
        return JSONResponse(content=points)

    # ------------------------------------------------------------------
    # GET /api/pnl?session_id= — cumulative P&L time series (Chart.js JSON)
    # ------------------------------------------------------------------

    @app.get("/api/pnl")
    async def get_pnl(session_id: str = "", algo_name: str = "") -> JSONResponse:
        async def _produce() -> str:
            from trading.reports.fetch import fetch_nifty_benchmark
            from trading.reports.pnl import DEFAULT_COSTS

            today = _today_start()

            async with session_factory() as session:
                conditions: list[ColumnElement[bool]] = [
                    Order.status == "FILLED",
                    Order.created_at >= today,
                ]
                if algo_name:
                    conditions.append(Signal.algo_name == algo_name)
                result = await session.execute(
                    select(Order, Signal)
                    .join(Signal, Order.signal_id == Signal.id)
                    .where(*conditions)
                    .order_by(Order.created_at)
                )
                rows = result.all()
                nifty = await fetch_nifty_benchmark(session, today, clock.now())

            cum_gross = 0.0
            cum_net = 0.0
            points: list[dict[str, object]] = []
            for order, signal in rows:
                sign = 1.0 if signal.side == "SELL" else -1.0
                notional = float(order.avg_price) * order.qty
                cost = DEFAULT_COSTS.cost_for_fill(signal.side, order.qty, float(order.avg_price))
                cum_gross += sign * notional
                cum_net += sign * notional - cost
                ts = order.created_at
                points.append(
                    {
                        "ts": ts.isoformat() if ts else "",
                        "cumulative_gross": round(cum_gross, 2),
                        "cumulative_net": round(cum_net, 2),
                        "side": signal.side,
                        "qty": order.qty,
                        "price": float(order.avg_price),
                        "cost": round(cost, 2),
                        "symbol": signal.symbol,
                        "signal_type": signal.signal_type,
                    }
                )

            total_costs = round(cum_gross - cum_net, 2)
            summary: dict[str, object] = {
                "gross": round(cum_gross, 2),
                "costs": total_costs,
                "net": round(cum_net, 2),
                "nifty_pct": round(nifty["pct_return"], 2) if nifty else None,
                "nifty_open": round(nifty["open"], 2) if nifty else None,
                "nifty_close": round(nifty["close"], 2) if nifty else None,
            }
            return json.dumps({"points": points, "summary": summary})

        if cacher_factory is not None:
            today_iso = clock.now().date().isoformat()
            body = await cacher_factory.api().get_or_set_response(
                key_args=("pnl", today_iso, session_id, algo_name),
                producer=_produce,
                ttl=30,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    # ------------------------------------------------------------------
    # GET /api/pnl/by-algo — per-algo P&L summary (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/pnl/by-algo")
    async def get_pnl_by_algo() -> JSONResponse:
        async def _produce() -> str:
            from trading.reports.pnl import DEFAULT_COSTS

            today = _today_start()

            async with session_factory() as session:
                result = await session.execute(
                    select(Order, Signal)
                    .join(Signal, Order.signal_id == Signal.id)
                    .where(
                        Order.status == "FILLED",
                        Order.created_at >= today,
                    )
                    .order_by(Order.created_at)
                )
                rows = result.all()

            by_algo: dict[str, dict[str, float]] = {}
            for order, signal in rows:
                name = signal.algo_name or "default"
                if name not in by_algo:
                    by_algo[name] = {"gross": 0.0, "costs": 0.0, "net": 0.0}
                sign = 1.0 if signal.side == "SELL" else -1.0
                notional = float(order.avg_price) * order.qty
                cost = DEFAULT_COSTS.cost_for_fill(signal.side, order.qty, float(order.avg_price))
                by_algo[name]["gross"] += sign * notional
                by_algo[name]["net"] += sign * notional - cost
                by_algo[name]["costs"] += cost

            return json.dumps({
                name: {
                    "gross": round(v["gross"], 2),
                    "costs": round(v["costs"], 2),
                    "net": round(v["net"], 2),
                }
                for name, v in by_algo.items()
            })

        if cacher_factory is not None:
            today_iso = clock.now().date().isoformat()
            body = await cacher_factory.api().get_or_set_response(
                key_args=("pnl:by_algo", today_iso),
                producer=_produce,
                ttl=30,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    # ------------------------------------------------------------------
    # GET /api/charts?session_id=&limit= — indicator chart series (JSON)
    # ------------------------------------------------------------------

    @app.get("/api/charts")
    async def get_charts(session_id: str = "", algo_name: str = "", limit: int = 500) -> JSONResponse:
        from trading.storage.stores.chart import ChartStore

        chart_store = ChartStore(session_factory)
        sid: str | None = session_id if session_id else None
        since = _today_start()

        async with session_factory() as session:
            result = await session.execute(select(AlgoConfig.name))
            all_algo_names = [r[0] for r in result.fetchall()]

        algo_names = [algo_name] if algo_name else all_algo_names

        combined: dict[str, dict[str, list[dict[str, object]]]] = {}
        for name in algo_names:
            chart_names = await chart_store.get_chart_names(name, since, sid)
            for chart_name in chart_names:
                series = await chart_store.get_indicator_series(
                    name, chart_name, since, sid, limit
                )
                if chart_name not in combined:
                    combined[chart_name] = {}
                combined[chart_name].update(series)

        return JSONResponse(content=combined)

    # ------------------------------------------------------------------
    # GET /api/decisions/stream?session_id= — SSE live decision feed
    # ------------------------------------------------------------------

    @app.get("/api/decisions/stream")
    async def decisions_stream(request: Request, session_id: str = "", algo_name: str = "") -> StreamingResponse:
        async def _event_generator() -> AsyncIterator[str]:
            yield ": connected\n\n"  # triggers EventSource.onopen immediately
            last_id = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    async with session_factory() as session:
                        conditions = [
                            DecisionLog.id > last_id,
                            DecisionLog.created_at >= _today_start(),
                            _session_filter(DecisionLog, session_id),
                        ]
                        if algo_name:
                            conditions.append(DecisionLog.algo_name == algo_name)
                        stmt = (
                            select(DecisionLog)
                            .where(*conditions)
                            .order_by(DecisionLog.id)
                            .limit(20)
                        )
                        result = await session.execute(stmt)
                        new_rows = result.scalars().all()

                    for row in new_rows:
                        last_id = row.id
                        payload = json.dumps(
                            {
                                "id": row.id,
                                "tick_log_id": row.tick_log_id,
                                "step": row.step,
                                "symbol": row.symbol,
                                "algo": row.algo_name,
                                "ts": row.created_at.isoformat() if row.created_at else None,
                                "context": json.loads(row.context) if row.context else {},
                            }
                        )
                        yield f"data: {payload}\n\n"
                except Exception as exc:
                    logger.debug("SSE generator error: %s", exc)
                from anyio import sleep as _asleep
                await _asleep(2)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # GET /api/reports/sessions — list all report sessions from results_dir
    # ------------------------------------------------------------------

    @app.get("/api/reports/sessions")
    async def get_report_sessions() -> JSONResponse:
        sessions: list[dict[str, object]] = []
        if not _results_dir.exists():
            return JSONResponse(content=sessions)
        for report_file in sorted(_results_dir.glob("*/report.json")):
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "session_id": data.get("session_id", ""),
                        "session_type": data.get("session_type", ""),
                        "algo_name": data.get("algo_name", ""),
                        "started_at": data.get("started_at", ""),
                        "finished_at": data.get("finished_at", ""),
                    }
                )
            except Exception:
                logger.debug("Skipping malformed report: %s", report_file)
        return JSONResponse(content=sessions)

    # ------------------------------------------------------------------
    # GET /api/reports/live?period=day|week|month&date=YYYY-MM-DD
    # NOTE: must be registered BEFORE /api/reports/{session_id} so the
    # literal path segment "live" is not captured as a session_id.
    # ------------------------------------------------------------------

    @app.get("/api/reports/live")
    async def get_live_report(
        period: str = "day",
        date: str = "",
    ) -> JSONResponse:
        if date:
            target_date = datetime.fromisoformat(date).replace(tzinfo=UTC)
        else:
            target_date = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if period == "day":
            start = target_date
            end = target_date.replace(hour=23, minute=59, second=59)
        elif period == "week":
            start = target_date - __import__("datetime").timedelta(days=target_date.weekday())
            end = start + __import__("datetime").timedelta(days=6, hours=23, minutes=59, seconds=59)
        elif period == "month":
            import calendar

            start = target_date.replace(day=1)
            last_day = calendar.monthrange(target_date.year, target_date.month)[1]
            end = target_date.replace(day=last_day, hour=23, minute=59, second=59)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown period: {period!r}")

        async def _produce() -> str:
            from trading.reports.engine import fetch_report_data

            data = await fetch_report_data(start, end, session_factory)
            return json.dumps(data)

        if cacher_factory is not None:
            body = await cacher_factory.api().get_or_set_response(
                key_args=("report", period, target_date.date().isoformat()),
                producer=_produce,
                ttl=60,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    # ------------------------------------------------------------------
    # GET /api/reports/{session_id} — full report JSON for one session
    # NOTE: registered after /api/reports/live so "live" is not matched here.
    # ------------------------------------------------------------------

    @app.get("/api/reports/{session_id}")
    async def get_report(session_id: str) -> JSONResponse:
        report_file = _results_dir / session_id / "report.json"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail=f"Report not found: {session_id}")
        data = json.loads(report_file.read_text(encoding="utf-8"))
        return JSONResponse(content=data)

    # ------------------------------------------------------------------
    # POST /api/postback — Zerodha order-update webhook
    #
    # Zerodha sends a POST to this URL when an order status changes.
    # On COMPLETE (filled), we call order_executor.handle_fill() to update
    # the order status and position — the same path that PaperBroker
    # triggers inline during paper trading.
    # ------------------------------------------------------------------

    @app.post("/api/postback")
    async def postback(request: Request) -> JSONResponse:
        if order_executor is None:
            raise HTTPException(status_code=503, detail="OrderExecutor not wired to postback endpoint")

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        status = payload.get("status", "")
        if status != "COMPLETE":
            return JSONResponse(content={"ok": True, "skipped": True, "status": status})

        kite_order_id: str = payload.get("order_id", "")
        avg_price: float = float(payload.get("average_price", 0))
        filled_qty: int = int(payload.get("filled_quantity", 0))
        symbol: str = payload.get("tradingsymbol", "")
        instrument_type: str = payload.get("instrument_type", "EQUITY")
        transaction_type: str = payload.get("transaction_type", "BUY")
        tick_log_id: int = int(payload.get("tick_log_id", 0))

        if not kite_order_id or not symbol or filled_qty <= 0 or avg_price <= 0:
            raise HTTPException(status_code=400, detail="Missing required fill fields")

        await order_executor.handle_fill(
            kite_order_id=kite_order_id,
            avg_price=avg_price,
            filled_qty=filled_qty,
            symbol=symbol,
            instrument_type=instrument_type,
            side=transaction_type,
            tick_log_id=tick_log_id,
        )
        return JSONResponse(content={"ok": True})

    return app


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _session_filter(model: type[DecisionLog], session_id: str) -> ColumnElement[bool]:
    if session_id:
        return model.session_id == session_id
    return model.session_id.is_(None)


