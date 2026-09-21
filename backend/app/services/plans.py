"""Plan limits and feature flags, read from the database (never hardcoded).

Both change rarely, so each process keeps a copy for ``CACHE_TTL_S``. An admin change
takes effect everywhere within that window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeatureFlag, Plan

CACHE_TTL_S = 60
UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class PlanInfo:
    code: str
    limits: dict[str, Any]
    features: frozenset[str]

    def limit(self, name: str) -> int:
        value = self.limits.get(name, 0)
        return int(value) if isinstance(value, int | float) else 0

    def allows(self, name: str, current_count: int) -> bool:
        limit = self.limit(name)
        return limit == UNLIMITED or current_count < limit


_plans: tuple[float, dict[str, PlanInfo]] = (0.0, {})
_flags: tuple[float, dict[str, Any]] = (0.0, {})


def clear_caches() -> None:
    global _plans, _flags
    _plans = (0.0, {})
    _flags = (0.0, {})


async def get_plan(session: AsyncSession, code: str) -> PlanInfo:
    global _plans
    loaded_at, plans = _plans
    if time.monotonic() - loaded_at > CACHE_TTL_S or code not in plans:
        rows = (await session.scalars(select(Plan))).all()
        plans = {
            p.code: PlanInfo(code=p.code, limits=dict(p.limits), features=frozenset(p.features))
            for p in rows
        }
        _plans = (time.monotonic(), plans)
    return plans.get(code) or plans["free"]


async def get_flag(session: AsyncSession, key: str, default: Any) -> Any:
    global _flags
    loaded_at, flags = _flags
    if time.monotonic() - loaded_at > CACHE_TTL_S:
        rows = (await session.scalars(select(FeatureFlag))).all()
        flags = {f.key: f.value for f in rows}
        _flags = (time.monotonic(), flags)
    return flags.get(key, default)
