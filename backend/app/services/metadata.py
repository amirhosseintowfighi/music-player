"""Filling in album, year and genre from the files themselves.

Telegram's audio metadata has a title, a performer and a duration — never an album,
a year or a genre (ARCHITECTURE §18). Those live in the file's ID3 tags, which only
the edge can read, so the core picks tracks that are missing them, asks an edge to
read the header, and writes back whatever came out.

Rules that keep this from doing damage:

- **Never overwrite.** Only NULL columns are filled. A tag that disagrees with what we
  already know is not better information, just different.
- **Never re-ask.** A track that was probed and had nothing gets ``metadata_probed_at``
  anyway, so the job moves on instead of re-reading the same file every night.
- **Small and slow.** A bounded batch per run: this reads real bytes over MTProto and
  competes with playback for the same accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import NotFound
from app.services import stream
from app.services.stream import build_ticket
from tmusic_common.logging import get_logger
from tmusic_common.stream_ticket import sign

log = get_logger(__name__)

# Each probe reads up to 256 KB, so a run is ~25 MB — the one job here that moves
# real bytes. It stops on its own when every track has been probed once.
BATCH = 100
TIMEOUT_S = 60.0
TICKET_TTL_S = 120


@dataclass(frozen=True, slots=True)
class Probed:
    track_id: int
    album: str | None
    year: int | None
    genre: str | None
    # The file carries its own cover. Telegram only makes a thumbnail for some
    # messages, so this is often the only artwork a crawled track has.
    has_artwork: bool = False


async def candidates(session: AsyncSession, limit: int = BATCH) -> list[int]:
    """Playable tracks missing tag data or a cover, that have never been probed.

    ``NOT has_thumb`` is in the list because the embedded cover is found by the same
    read: a track with an album but no artwork still has a blank square to fix.
    """
    rows = await session.execute(
        text(
            """
        SELECT id FROM tracks
         WHERE canonical_track_id IS NULL AND NOT hidden AND playable
           AND metadata_probed_at IS NULL
           AND (album IS NULL OR year IS NULL OR genre IS NULL OR NOT has_thumb)
           AND file_size BETWEEN 100000 AND 100000000
         ORDER BY channels_count DESC, id
         LIMIT :limit
        """
        ).bindparams(limit=limit)
    )
    return [row[0] for row in rows]


async def apply(session: AsyncSession, probed: Probed) -> bool:
    """Writes tags into the empty columns only. Returns True when something changed."""
    result = await session.execute(
        text(
            """
        UPDATE tracks SET
            album = coalesce(album, :album),
            normalized_album = coalesce(normalized_album, nullif(lower(:album), '')),
            year  = coalesce(year, :year),
            genre = coalesce(genre, :genre),
            has_thumb = has_thumb OR :artwork,
            metadata_probed_at = now(),
            updated_at = now()
         WHERE id = :id
        RETURNING (album IS NOT NULL OR year IS NOT NULL OR genre IS NOT NULL
                   OR has_thumb) AS filled
        """
        ).bindparams(
            id=probed.track_id,
            album=(probed.album or None),
            year=probed.year,
            genre=(probed.genre or None),
            artwork=probed.has_artwork,
        )
    )
    row = result.first()
    return bool(row and row.filled)


async def mark_probed(session: AsyncSession, track_ids: list[int]) -> None:
    """Even a track with no tags is 'done': re-reading it nightly would be waste."""
    if not track_ids:
        return
    await session.execute(
        text("UPDATE tracks SET metadata_probed_at = now() WHERE id = ANY(:ids)").bindparams(
            ids=track_ids
        )
    )


async def probe_batch(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = BATCH
) -> dict[str, int]:
    """Probes one batch of tracks through the edge. Returns counters for the job log."""
    track_ids = await candidates(session, limit)
    if not track_ids:
        return {"probed": 0, "filled": 0, "failed": 0}

    filled = failed = 0
    done: list[int] = []
    for track_id in track_ids:
        try:
            # Built by hand rather than through issue_ticket: a probe is not a play and
            # must not count against anybody's daily limit.
            source = await stream.pick_source(
                session, track_id, settings.bot_id, settings.bot_api_max_download
            )
            ticket = build_ticket(source, 0, TICKET_TTL_S)
            token = sign(ticket, settings.signing_keys[0])
        except (NotFound, LookupError, ValueError):
            log.info("metadata.no_source", track_id=track_id)
            failed += 1
            continue
        try:
            response = await http.post(
                f"{settings.edge_internal_url.rstrip('/')}/internal/probe",
                json={"ticket": token},
                headers={
                    "Authorization": f"Bearer {settings.internal_api_token.get_secret_value()}"
                },
                timeout=TIMEOUT_S,
            )
            if response.status_code != 200:
                failed += 1
                continue
            body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError):
            log.info("metadata.probe_failed", track_id=track_id, exc_info=True)
            failed += 1
            continue

        done.append(track_id)
        if await apply(
            session,
            Probed(
                track_id=track_id,
                album=body.get("album"),
                year=body.get("year"),
                genre=body.get("genre"),
                has_artwork=bool(body.get("has_artwork")),
            ),
        ):
            filled += 1

    await mark_probed(session, done)
    log.info("metadata.probed", tracks=len(done), filled=filled, failed=failed)
    return {"probed": len(done), "filled": filled, "failed": failed}
