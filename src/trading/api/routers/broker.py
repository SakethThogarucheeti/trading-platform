from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from trading.execution.fill_webhook import WebhookValidationError, parse_fill_payload
from trading.execution.order_executor import OrderExecutor


def create_broker_router(order_executor: OrderExecutor | None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/postback")
    async def postback(request: Request) -> JSONResponse:
        if order_executor is None:
            raise HTTPException(
                status_code=503, detail="OrderExecutor not wired to postback endpoint"
            )

        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

        try:
            fill = parse_fill_payload(raw)
        except WebhookValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if fill is None:
            return JSONResponse(
                content={"ok": True, "skipped": True, "status": raw.get("status", "")}
            )

        await order_executor.handle_fill(
            kite_order_id=fill.kite_order_id,
            avg_price=fill.avg_price,
            filled_qty=fill.filled_qty,
            symbol=fill.symbol,
            instrument_type=fill.instrument_type,
            side=fill.side,
            tick_log_id=fill.tick_log_id,
        )
        return JSONResponse(content={"ok": True})

    return router
