from __future__ import annotations

from fastapi import APIRouter

from app import metrics
from app.api.deps import Claims, HttpDep, RedisDep, SessionDep, SettingsDep
from app.schemas import PlaybackEventIn, StreamOut, ThumbsIn, ThumbsOut
from app.services import stream

router = APIRouter(prefix="/v1", tags=["stream"])


@router.post("/tracks/thumbs", response_model=ThumbsOut)
async def track_thumbs(
    body: ThumbsIn, claims: Claims, session: SessionDep, settings: SettingsDep
) -> ThumbsOut:
    """Artwork URLs for a list of tracks. Does not count towards the daily play limit."""
    items = await stream.thumbnail_urls(session, settings, claims.user_id, body.ids)
    return ThumbsOut(items=items)


@router.post("/tracks/{track_id}/stream", response_model=StreamOut)
async def stream_ticket(
    track_id: int,
    claims: Claims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    http: HttpDep,
    prefetch: bool = False,
) -> StreamOut:
    """Signed URL on a streaming edge. Short TTL; the client asks again when it expires.

    The bytes never pass through this API (ADR-0004): the URL points at an edge node
    outside the core network, and `/api/stream/:id` is that two-step contract, not a
    route here. `tests/unit/test_no_bytes_through_core.py` fails the build if that
    ever changes.

    `?prefetch=1` warms the next track up: it does not count against the daily limit
    and the ticket it returns can only read the first few hundred kilobytes.
    """
    return await stream.issue_ticket(
        session, redis, settings, claims, track_id, http, prefetch=prefetch
    )


@router.post("/telemetry/playback", status_code=204)
async def playback_telemetry(body: PlaybackEventIn, claims: Claims) -> None:
    """What only the client can see: time to first sound, stalls, and failures.

    Deliberately writes nothing to the database — it increments Prometheus counters
    and returns. A player in trouble must not also become database load.
    """
    if body.kind == "start" and body.ms is not None:
        metrics.PLAYBACK_START.observe(min(body.ms, 60_000) / 1000)
    elif body.kind == "underrun":
        metrics.PLAYBACK_UNDERRUNS.inc()
    elif body.kind == "error":
        metrics.PLAYBACK_ERRORS.labels(body.reason or "unknown").inc()
