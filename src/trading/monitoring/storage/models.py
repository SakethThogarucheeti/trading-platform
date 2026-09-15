from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    module: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FailedDispatch(Base):
    """
    A background dispatch (see trading.app.tasks.fire) that failed at a call
    site with no fallback recovery path -- e.g. a decision-log write or a
    paper-fill postback. Recorded so a human/reconciliation job can replay it
    from *payload* later instead of the failure being silently unrecoverable
    (trading-platform#94).
    """

    __tablename__ = "failed_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
