from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import MeiliDep, RedisDep, State
from tmusic_common.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(
    state: State, redis: RedisDep, meili: MeiliDep, response: Response
) -> dict[str, bool]:
    checks = {"db": False, "redis": False, "search": False}
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:
        log.warning("readyz.db_failed", exc_info=True)
    try:
        checks["redis"] = bool(await redis.ping())
    except Exception:
        log.warning("readyz.redis_failed", exc_info=True)
    checks["search"] = await meili.healthy()
    # Search has a Postgres fallback, so only db + redis gate readiness.
    if not (checks["db"] and checks["redis"]):
        response.status_code = 503
    return checks


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    # Exposed on the internal interface only (nginx denies it publicly).
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
