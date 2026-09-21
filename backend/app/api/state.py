"""Process-wide resources, created once in the app lifespan."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.meili import MeiliClient


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    meili: MeiliClient
