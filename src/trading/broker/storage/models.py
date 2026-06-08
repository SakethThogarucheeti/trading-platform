from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BrokerToken(Base):
    """
    Encrypted broker access token stored in Postgres.

    ``token_enc`` holds the pgcrypto-encrypted ciphertext written via
    ``pgp_sym_encrypt(token, key)`` and read back with ``pgp_sym_decrypt``.
    The encryption key lives in the ``TOKEN_SECRET_KEY`` env var and never
    touches the DB.
    """

    __tablename__ = "broker_tokens"

    broker: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "zerodha"
    token_enc: Mapped[str] = mapped_column(String)                  # pgcrypto ciphertext
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
