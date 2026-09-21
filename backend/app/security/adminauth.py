"""Admin authentication: Telegram Login Widget + a separate admin JWT.

The admin panel is a normal web app, not a Mini App, so it authenticates with the
**Login Widget**, whose signature differs from initData: the secret is
``SHA256(bot_token)`` and the payload is a flat object, not a query string
(https://core.telegram.org/widgets/login#checking-authorization).

Admin tokens are a different token type (``typ: "admin"``) signed with the same
Ed25519 key, so a user access token can never be replayed against an admin endpoint
and vice versa.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt

from app.security.tokens import ALGORITHM, TokenError

MAX_LOGIN_AGE_S = 300  # a widget payload older than this is a replay


class LoginError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LoginPayload:
    tg_id: int
    first_name: str
    username: str | None
    photo_url: str | None


@dataclass(frozen=True, slots=True)
class AdminClaims:
    admin_id: int
    tg_id: int
    role: str
    permissions: tuple[str, ...]


def verify_login_widget(
    data: dict[str, Any], bot_token: str, max_age_s: int = MAX_LOGIN_AGE_S, now: float | None = None
) -> LoginPayload:
    fields = {str(k): v for k, v in data.items() if v is not None}
    received = str(fields.pop("hash", "") or "")
    if not received:
        raise LoginError("missing hash")
    auth_date = fields.get("auth_date")
    if auth_date is None:
        raise LoginError("missing auth_date")

    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise LoginError("bad signature")

    age = (time.time() if now is None else now) - int(auth_date)
    if age > max_age_s or age < -60:
        raise LoginError("stale login")

    tg_id = fields.get("id")
    if tg_id is None:
        raise LoginError("missing id")
    return LoginPayload(
        tg_id=int(tg_id),
        first_name=str(fields.get("first_name", "")),
        username=str(fields["username"]) if fields.get("username") else None,
        photo_url=str(fields["photo_url"]) if fields.get("photo_url") else None,
    )


def encode_admin(
    claims: AdminClaims, private_key: str, issuer: str, ttl_s: int, now: float | None = None
) -> tuple[str, int]:
    iat = int(time.time() if now is None else now)
    exp = iat + ttl_s
    payload = {
        "sub": str(claims.admin_id),
        "tg": claims.tg_id,
        "role": claims.role,
        "perm": list(claims.permissions),
        "iss": issuer,
        "iat": iat,
        "exp": exp,
        "jti": uuid.uuid4().hex,
        "typ": "admin",
    }
    return jwt.encode(payload, private_key, algorithm=ALGORITHM), exp


def decode_admin(token: str, public_key: str, issuer: str) -> AdminClaims:
    try:
        data = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if data.get("typ") != "admin":
        raise TokenError("not an admin token")
    return AdminClaims(
        admin_id=int(data["sub"]),
        tg_id=int(data.get("tg", 0)),
        role=str(data.get("role", "")),
        permissions=tuple(str(p) for p in data.get("perm", [])),
    )
