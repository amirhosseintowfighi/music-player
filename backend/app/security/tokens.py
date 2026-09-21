"""Access JWTs (EdDSA, short-lived) and opaque refresh tokens (ADR-0007)."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt

ALGORITHM = "EdDSA"


class TokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: int
    tg_id: int
    plan: str
    lang: Literal["fa", "en"]
    features: tuple[str, ...]
    act_as_admin: int | None = None  # set on impersonation tokens (read-only)


def encode_access(
    claims: AccessClaims, private_key: str, issuer: str, ttl_s: int, now: float | None = None
) -> tuple[str, int]:
    iat = int(time.time() if now is None else now)
    exp = iat + ttl_s
    payload: dict[str, object] = {
        "sub": str(claims.user_id),
        "tg": claims.tg_id,
        "plan": claims.plan,
        "lang": claims.lang,
        "ft": list(claims.features),
        "iss": issuer,
        "iat": iat,
        "exp": exp,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    if claims.act_as_admin is not None:
        payload["act"] = claims.act_as_admin
    return jwt.encode(payload, private_key, algorithm=ALGORITHM), exp


def decode_access(token: str, public_key: str, issuer: str) -> AccessClaims:
    try:
        data = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        if data.get("typ") != "access":
            raise TokenError("wrong token type")
        lang = data["lang"]
        if lang not in ("fa", "en"):
            raise TokenError("bad lang")
        act = data.get("act")
        return AccessClaims(
            user_id=int(data["sub"]),
            tg_id=int(data["tg"]),
            plan=str(data["plan"]),
            lang=lang,
            features=tuple(str(f) for f in data.get("ft", [])),
            act_as_admin=int(act) if act is not None else None,
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise TokenError(str(exc)) from exc


def new_refresh_token() -> tuple[str, bytes]:
    """Return (token for the client, hash for the database)."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
