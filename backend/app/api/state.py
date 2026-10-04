"""Process-wide resources, created once in the app lifespan."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.meili import MeiliClient

MAINTENANCE_REDIS_KEY = "flag:maintenance_mode"


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    meili: MeiliClient
    _maintenance_cached_at: float = 0.0
    _maintenance_cached_value: bool = False

    def maintenance_cache_get(self) -> bool | None:
        if time.monotonic() - self._maintenance_cached_at < 60:
            return self._maintenance_cached_value
        return None

    def maintenance_cache_set(self, value: bool) -> None:
        self._maintenance_cached_at = time.monotonic()
        self._maintenance_cached_value = bool(value)
