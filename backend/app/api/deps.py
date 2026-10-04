from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import AppState, MAINTENANCE_REDIS_KEY
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


_RATE_LIMIT_LUA = """
local cur = redis.call('GET', KEYS[1])
local prev = redis.call('GET', KEYS[2])
cur = tonumber(cur or '0')
prev = tonumber(prev or '0')
local now_ms = tonumber(ARGV[2])
local weight = 1 - ((now_ms % 60000) / 60000)
if weight < 0 then weight = 0 end
if weight > 1 then weight = 1 end
local estimated = cur + prev * weight
if estimated >= tonumber(ARGV[1]) then
  return 0
end
local new_val = redis.call('INCR', KEYS[1])
redis.call('PEXPIRE', KEYS[1], 65000)
return 1
"""


async def _hit(redis: Redis, key: str, limit: int) -> None:
    now_ms = int(time.time() * 1000)
    cur_bucket = f"rl:{key}:{int(time.time() // 60)}"
    prev_bucket = f"rl:{key}:{int(time.time() // 60) - 1}"
    try:
        # redis.eval signature varies; try both eval and evalsha style
        result = await redis.eval(
            _RATE_LIMIT_LUA, 2, cur_bucket, prev_bucket, str(limit), str(now_ms)
        )
        # fakeredis returns int, redis-py returns int
        allowed = int(result) if result is not None else 1
        if allowed == 0:
            raise RateLimited("too many requests", retry_after=60 - int(time.time()) % 60)
        return
    except RateLimited:
        raise
    except Exception:  # noqa: BLE001
        # Fallback for fakeredis or Redis without EVAL support: fixed window
        bucket = cur_bucket
        count = await resolve(redis.incr(bucket))
        if count == 1:
            await resolve(redis.expire(bucket, 65))
        if count > limit:
            raise RateLimited("too many requests", retry_after=60 - int(time.time()) % 60)


async def rate_limit_ip(request: Request, redis: RedisDep, settings: SettingsDep) -> None:
    await _hit(redis, f"ip:{client_ip(request)}", settings.rate_limit_ip_per_min)


async def rate_limit_auth(request: Request, redis: RedisDep, settings: SettingsDep) -> None:
    await _hit(redis, f"auth:{client_ip(request)}", settings.rate_limit_auth_per_min)


async def maintenance_gate(request: Request, state: State) -> None:
    """503 for user traffic while `maintenance_mode` is on; admins keep working."""
    path = request.url.path
    if path.startswith(("/admin", "/healthz", "/readyz", "/metrics", "/tg/", "/internal")):
        return
    # 1. AppState in-memory cache (60s)
    cached = state.maintenance_cache_get()
    if cached is not None:
        if cached:
            raise Unavailable("maintenance", reason="maintenance_mode")
        return
    # 2. Redis
    try:
        raw = await resolve(state.redis.get(MAINTENANCE_REDIS_KEY))
    except Exception:  # noqa: BLE001
        raw = None
    if raw is not None:
        # Redis stores "1"/"0" with EX 60
        if isinstance(raw, bytes):
            raw = raw.decode()
        is_on = raw == "1" or raw == 1 or raw is True or raw == "true"
        state.maintenance_cache_set(is_on)
        if is_on:
            raise Unavailable("maintenance", reason="maintenance_mode")
        return
    # 3. Fallback DB (only on cache miss)
    async with session_scope(state.sessionmaker) as session:
        flag = await plans.get_flag(session, "maintenance_mode", False)
        is_on = bool(flag)
        # populate both caches
        try:
            await resolve(state.redis.set(MAINTENANCE_REDIS_KEY, "1" if is_on else "0", ex=60))
        except Exception:  # noqa: BLE001
            pass
        state.maintenance_cache_set(is_on)
        if is_on:
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
