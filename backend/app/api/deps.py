from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import AppState
from app.config import Settings
from app.db import session_scope
from app.errors import Forbidden, RateLimited, Unauthorized, Unavailable
from app.redis_util import BANNED_SET, resolve
from app.security.tokens import AccessClaims, TokenError, decode_access
from app.services import plans
from app.services.meili import MeiliClient

_bearer = HTTPBearer(auto_error=False)


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.app
    return state


State = Annotated[AppState, Depends(get_state)]


def get_settings_dep(state: State) -> Settings:
    return state.settings


def get_redis(state: State) -> Redis:
    return state.redis


def get_meili(state: State) -> MeiliClient:
    return state.meili


def get_http(state: State) -> httpx.AsyncClient:
    return state.http


async def get_session(state: State) -> AsyncIterator[AsyncSession]:
    async with session_scope(state.sessionmaker) as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RedisDep = Annotated[Redis, Depends(get_redis)]
MeiliDep = Annotated[MeiliClient, Depends(get_meili)]
HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    # uvicorn runs with --proxy-headers behind nginx, so request.client is the real peer.
    return request.client.host if request.client else "unknown"


async def _hit(redis: Redis, key: str, limit: int) -> None:
    bucket = f"rl:{key}:{int(time.time() // 60)}"
    count = await redis.incr(bucket)
    if count == 1:
        await redis.expire(bucket, 65)
    if count > limit:
        raise RateLimited("too many requests", retry_after=60 - int(time.time()) % 60)


async def rate_limit_ip(request: Request, redis: RedisDep, settings: SettingsDep) -> None:
    await _hit(redis, f"ip:{client_ip(request)}", settings.rate_limit_ip_per_min)


async def maintenance_gate(request: Request, state: State) -> None:
    """503 for user traffic while ``maintenance_mode`` is on; admins keep working.

    The flag is cached for a minute inside ``plans.get_flag``, so this costs nothing
    on the hot path.
    """
    path = request.url.path
    if path.startswith(("/admin", "/healthz", "/readyz", "/metrics", "/tg/", "/internal")):
        return
    async with session_scope(state.sessionmaker) as session:
        if await plans.get_flag(session, "maintenance_mode", False):
            raise Unavailable("maintenance", reason="maintenance_mode")


async def current_claims(
    settings: SettingsDep,
    redis: RedisDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessClaims:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("missing bearer token")
    try:
        claims = decode_access(creds.credentials, settings.jwt_public_key, settings.jwt_issuer)
    except TokenError as exc:
        raise Unauthorized("invalid token") from exc
    if await resolve(redis.sismember(BANNED_SET, str(claims.user_id))):
        raise Forbidden("user is banned")
    await _hit(redis, f"u:{claims.user_id}", settings.rate_limit_user_per_min)
    return claims


async def writable_claims(claims: Annotated[AccessClaims, Depends(current_claims)]) -> AccessClaims:
    if claims.act_as_admin is not None:
        raise Forbidden("impersonation tokens are read-only")
    return claims


Claims = Annotated[AccessClaims, Depends(current_claims)]
WritableClaims = Annotated[AccessClaims, Depends(writable_claims)]
