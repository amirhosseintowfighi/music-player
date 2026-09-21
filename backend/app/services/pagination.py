"""Opaque keyset cursors. OFFSET is never used for listing (ARCHITECTURE §10-3)."""

from __future__ import annotations

import base64
import json
from datetime import datetime

from app.errors import InvalidInput

CursorValue = int | float | str | None


def encode_cursor(*values: CursorValue | datetime) -> str:
    plain = [v.isoformat() if isinstance(v, datetime) else v for v in values]
    return base64.urlsafe_b64encode(json.dumps(plain, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor: str | None, arity: int) -> list[CursorValue] | None:
    if not cursor:
        return None
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError as exc:
        raise InvalidInput("invalid cursor") from exc
    if not isinstance(values, list) or len(values) != arity:
        raise InvalidInput("invalid cursor")
    if not all(isinstance(v, int | float | str) or v is None for v in values):
        raise InvalidInput("invalid cursor")
    return values


def cursor_datetime(value: CursorValue) -> datetime:
    if not isinstance(value, str):
        raise InvalidInput("invalid cursor")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInput("invalid cursor") from exc


def cursor_int(value: CursorValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInput("invalid cursor")
    return value
