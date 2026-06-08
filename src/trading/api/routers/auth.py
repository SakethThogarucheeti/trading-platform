from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.service.zerodha.kite_client import KiteClient
from trading.execution.storage.store import TradingStore
from trading.tick_ingest.api import KiteIngestor


class _CallbackRequest(BaseModel):
    request_token: str


def create_auth_router(
    session_factory: async_sessionmaker[AsyncSession],
    zerodha_api_key: str,
    zerodha_api_secret: str,
    token_secret_key: str,
    kite_client: KiteClient | None,
    kite_ingestor: KiteIngestor | None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/auth/login-url")
    async def get_login_url() -> JSONResponse:
        if not zerodha_api_key:
            raise HTTPException(status_code=503, detail="ZERODHA_API_KEY not configured")
        url = KiteClient(zerodha_api_key).login_url()
        return JSONResponse(content={"url": url})

    @router.post("/api/auth/callback")
    async def auth_callback(body: _CallbackRequest) -> JSONResponse:
        if not zerodha_api_key or not zerodha_api_secret:
            raise HTTPException(status_code=503, detail="Zerodha credentials not configured")
        if not token_secret_key:
            raise HTTPException(status_code=503, detail="TOKEN_SECRET_KEY not configured")

        exchange_client = KiteClient(zerodha_api_key)
        try:
            session = exchange_client.generate_session(body.request_token, zerodha_api_secret)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}") from exc

        access_token: str = session["access_token"]

        trading_store = TradingStore(session_factory)
        await trading_store.save_broker_token("zerodha", access_token, token_secret_key)

        if kite_client is not None:
            kite_client.set_access_token(access_token)

        if kite_ingestor is not None:
            asyncio.get_running_loop().create_task(kite_ingestor.reconnect_stream())

        return JSONResponse(
            content={
                "ok": True,
                "user_name": session.get("user_name", ""),
                "login_time": str(session.get("login_time", "")),
            }
        )

    return router
