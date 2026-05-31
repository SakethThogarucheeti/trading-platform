from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import AlgoConfig, AlgoState
from trading.storage.stores.config import ConfigStore


class _AlgoPatch(BaseModel):
    enabled: bool | None = None
    params: dict[str, object] | None = None


def create_algos_router(session_factory: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/algos")
    async def get_algos() -> JSONResponse:
        algos = await ConfigStore(session_factory).get_algo_configs_with_state()
        return JSONResponse(content=algos)

    @router.patch("/api/algos/{name}")
    async def patch_algo(name: str, body: _AlgoPatch) -> JSONResponse:
        async with session_factory() as session:
            async with session.begin():
                algo = await session.get(AlgoConfig, name)
                if algo is None:
                    raise HTTPException(status_code=404, detail=f"Algo not found: {name!r}")
                if body.enabled is not None:
                    algo.enabled = body.enabled
                if body.params is not None:
                    algo.params = json.dumps(body.params)
        return JSONResponse(content={"ok": True, "name": name})

    @router.post("/api/algos/{name}/reset-state")
    async def reset_algo_state(name: str) -> JSONResponse:
        async with session_factory() as session:
            async with session.begin():
                algo = await session.get(AlgoConfig, name)
                if algo is None:
                    raise HTTPException(status_code=404, detail=f"Algo not found: {name!r}")
                state = await session.get(AlgoState, name)
                if state is not None:
                    await session.delete(state)
        return JSONResponse(content={"ok": True, "name": name, "state_cleared": True})

    return router
