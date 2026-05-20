from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import Instrument


class AbstractInstrumentStore(ABC):
    @abstractmethod
    async def get_instrument(self, token: int) -> Instrument | None: ...

    @abstractmethod
    async def upsert_instruments(self, instruments: list[Instrument]) -> None: ...


class InstrumentStore(AbstractInstrumentStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_instrument(self, token: int) -> Instrument | None:
        async with self._sf() as session:
            return await session.get(Instrument, token)

    async def upsert_instruments(self, instruments: list[Instrument]) -> None:
        async with self._sf() as session:
            async with session.begin():
                for inst in instruments:
                    await session.merge(inst)
