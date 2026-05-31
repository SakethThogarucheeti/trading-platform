from __future__ import annotations

from sqlalchemy.sql.elements import ColumnElement

from trading.core.models import DecisionLog


def session_filter(model: type[DecisionLog], session_id: str) -> ColumnElement[bool]:
    if session_id:
        return model.session_id == session_id
    return model.session_id.is_(None)
