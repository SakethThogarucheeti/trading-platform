from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading.core.models import Order


async def is_duplicate(signal_id: UUID, session: AsyncSession) -> bool:
    """
    Return True if an Order row for *signal_id* already exists.

    Must be called inside an open transaction to prevent TOCTOU races.
    """
    result = await session.execute(select(Order.id).where(Order.signal_id == signal_id).limit(1))
    return result.first() is not None
