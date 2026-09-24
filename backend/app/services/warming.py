"""Putting the first second of a popular track into the cache before anyone plays it.

Measured on a live catalogue: with a quarter of a million tracks and a cache holding
a couple of thousand slices, nearly every play is a cache miss, so the first bytes
come live from Telegram while the listener waits — and when that fetch is slower
than the music, playback stalls mid-song.

The fix is unglamorous: fetch the first slice of the tracks people are most likely to
press play on, through the same public URL a player would use, so nginx caches it
under the same key. The next real play starts from disk.

Three things keep it from becoming the problem it solves:

- **It reads on the crawling account** (the ``X-Warm`` header), so warming never
  competes with somebody listening — which is the mistake this whole change exists
  to correct.
- **It warms one slice, not a file.** A megabyte covers the opening seconds; the rest
  arrives while those play.
- **It remembers what it warmed** in Redis, for slightly less than the cache's own
  lifetime, so a popular track is not re-fetched every ten minutes.
"""

from __future__ import annotations

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import NotFound
from app.services import stream
from tmusic_common.logging import get_logger
from tmusic_common.stream_ticket import sign

log = get_logger(__name__)

# nginx slices the file into 1 MiB pieces; warming anything else would populate a
# different key than the one a player asks for.
SLICE_BYTES = 1024 * 1024
BATCH = 20
TICKET_TTL_S = 300
TIMEOUT_S = 60.0
# Just under proxy_cache_valid, so a warmed slice is not re-fetched while it is still
# in the cache — and is refreshed shortly before it would expire.
REMEMBER_S = 6 * 24 * 3600

_CANDIDATES = text(
    """
    SELECT id
      FROM tracks
     WHERE canonical_track_id IS NULL AND NOT hidden AND playable
       AND resolve_status = 'resolved'
       AND file_size > :slice
     ORDER BY likes_count DESC, plays_7d DESC, id
     LIMIT :limit
    """
)


def _key(track_id: int) -> str:
    return f"warm:{track_id}"


async def warm_batch(
    session: AsyncSession,
    redis: Redis,
    http: httpx.AsyncClient,
    settings: Settings,
    limit: int = BATCH,
) -> dict[str, int]:
    """Warms the first slice of the most-liked tracks that are not warm already."""
    try:
        host = await stream.pick_edge(session)
    except Exception:  # noqa: BLE001 — no edge registered yet is not an error here
        return {"warmed": 0, "skipped": 0, "failed": 0}

    base = stream.edge_base_url(host)
    ids = list(await session.scalars(_CANDIDATES.bindparams(slice=SLICE_BYTES, limit=limit * 3)))
    warmed = skipped = failed = 0

    for track_id in ids:
        if warmed >= limit:
            break
        if await redis.get(_key(track_id)):
            skipped += 1
            continue
        try:
            source = await stream.pick_source(
                session, track_id, settings.bot_id, settings.bot_api_max_download
            )
            # max_bytes is the slice: a warming ticket can never stand in for a play.
            ticket = stream.build_ticket(source, 0, TICKET_TTL_S, max_bytes=SLICE_BYTES)
            token = sign(ticket, settings.signing_keys[0])
        except (NotFound, LookupError, ValueError):
            failed += 1
            continue

        try:
            response = await http.get(
                f"{base}/s/{track_id}",
                params={"t": token},
                headers={"Range": f"bytes=0-{SLICE_BYTES - 1}", "X-Warm": "1"},
                timeout=TIMEOUT_S,
            )
            body = response.content  # read it, so nginx finishes writing the entry
        except httpx.HTTPError as exc:
            log.info("warm.failed", track_id=track_id, error=str(exc))
            failed += 1
            continue

        if response.status_code not in (200, 206) or not body:
            failed += 1
            continue
        await redis.set(_key(track_id), "1", ex=REMEMBER_S)
        warmed += 1

    if warmed or failed:
        log.info("warm.done", warmed=warmed, skipped=skipped, failed=failed)
    return {"warmed": warmed, "skipped": skipped, "failed": failed}
