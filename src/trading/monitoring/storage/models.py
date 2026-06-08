from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    module: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
