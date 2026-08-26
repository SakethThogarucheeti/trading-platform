from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from trading.core.clock import Clock
from trading.core.models import DecisionLog

from ._helpers import decision_log_base_conditions

logger = logging.getLogger(__name__)


def create_stream_router(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/decisions/stream")
    async def decisions_stream(
        request: Request, session_id: str = "", algo_name: str = ""
    ) -> StreamingResponse:
        async def _event_generator() -> AsyncIterator[str]:
            yield ": connected\n\n"
            last_id = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    async with session_factory() as session:
                        conditions = decision_log_base_conditions(
                            clock,
                            session_id,
                            algo_name,
                            DecisionLog.id > last_id,
                        )
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

    return router
