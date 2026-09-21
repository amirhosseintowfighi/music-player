"""Telegram Mini App initData validation.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
The client-provided ``user.id`` is only trusted after this check passes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from pydantic import BaseModel, ValidationError

MAX_CLOCK_SKEW_S = 60


class InitDataError(Exception):
    pass


class TelegramUser(BaseModel):
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool = False
    photo_url: str | None = None


class InitData(BaseModel):
    user: TelegramUser
    auth_date: int
    query_id: str | None = None
    start_param: str | None = None
    chat_type: str | None = None


def validate_init_data(
    raw: str, bot_token: str, max_age_s: int, now: float | None = None
) -> InitData:
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("malformed") from exc
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InitDataError("duplicate keys")
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("missing hash")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("bad signature")

    try:
        auth_date = int(fields.get("auth_date", ""))
        user = json.loads(fields.get("user", ""))
        data = InitData(
            user=user,
            auth_date=auth_date,
            query_id=fields.get("query_id"),
            start_param=fields.get("start_param"),
            chat_type=fields.get("chat_type"),
        )
    except (ValueError, ValidationError) as exc:
        raise InitDataError("invalid payload") from exc

    current = time.time() if now is None else now
    if auth_date > current + MAX_CLOCK_SKEW_S:
        raise InitDataError("auth_date in the future")
    if current - auth_date > max_age_s:
        raise InitDataError("expired")
    return data


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a valid initData string. Used by tests and the local dev login helper."""
    from urllib.parse import urlencode

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})
