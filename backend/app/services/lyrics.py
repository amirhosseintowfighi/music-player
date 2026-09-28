"""Lyrics, synced line by line where possible, from LRCLIB (lrclib.net).

LRCLIB is an open, keyless lyrics database. A track is looked up once: the answer —
including "there are none" — is kept, so a play never waits on it twice. A network
failure is not an answer and is not kept.

Matching is by artist, title and duration; a search result is only accepted when its
length is within a few seconds of ours, so a live version or a remix does not lend
its words to the studio one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import NotFound
from app.models import Track, TrackLyrics
from app.services import history
from app.services.lastfm import title_key
from tmusic_common.logging import get_logger

log = get_logger(__name__)

TIMEOUT_S = 6.0
DURATION_SLACK_S = 4
# A miss is re-asked now and then: somebody may have added the song since.
RETRY_MISS_AFTER = timedelta(days=30)
USER_AGENT = "tmusic-miniapp (https://github.com/amirhosseintowfighi/music-player)"


@dataclass(frozen=True, slots=True)
class Lyrics:
    track_id: int
    synced: str | None
    plain: str | None
    source: str | None


def _pick(results: list[dict[str, Any]], duration: int) -> dict[str, Any] | None:
    """The best search hit: synced over plain, and only a close length match."""
    close = [
        r
        for r in results
        if not duration or abs(int(r.get("duration") or 0) - duration) <= DURATION_SLACK_S
    ]
    for want_synced in (True, False):
        for result in close:
            if result.get("instrumental"):
                continue
            if want_synced and result.get("syncedLyrics"):
                return result
            if not want_synced and result.get("plainLyrics"):
                return result
    return None


async def fetch(
    http: httpx.AsyncClient, base_url: str, artists: list[str], title: str, duration: int
) -> dict[str, Any] | None:
    """One exact lookup per artist spelling, then a search. None means "no lyrics"."""
    headers = {"User-Agent": USER_AGENT}
    base = base_url.rstrip("/")
    clean = title.strip()
    for artist in artists:
        params: dict[str, str | int] = {"artist_name": artist, "track_name": clean}
        if duration:
            params["duration"] = duration
        response = await http.get(
            f"{base}/api/get", params=params, headers=headers, timeout=TIMEOUT_S
        )
        if response.status_code == 200:
            found = _pick([response.json()], duration)
            if found:
                return found
        elif response.status_code != 404:
            response.raise_for_status()
    query = f"{artists[0]} {clean}" if artists else clean
    response = await http.get(
        f"{base}/api/search", params={"q": query}, headers=headers, timeout=TIMEOUT_S
    )
    response.raise_for_status()
    results = response.json()
    return _pick(results if isinstance(results, list) else [], duration)


async def _artist_names(session: AsyncSession, track_id: int) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT a.name, a.latin_name FROM track_artists ta"
            " JOIN artists a ON a.id = ta.artist_id"
            " WHERE ta.track_id = :t AND ta.role = 'primary' ORDER BY ta.position"
        ).bindparams(t=track_id)
    )
    names: list[str] = []
    for name, latin in rows.tuples():
        # The Latin spelling first: that is what LRCLIB mostly holds.
        for candidate in (latin, name):
            if candidate and candidate not in names:
                names.append(candidate)
    return names


async def get(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, track_id: int
) -> Lyrics | None:
    root = await history.canonical_id(session, track_id)
    track = await session.get(Track, root)
    if track is None or track.hidden:
        raise NotFound("track not found")
    cached = await session.get(TrackLyrics, root)
    fresh_miss = (
        cached is not None
        and not cached.found
        and (cached.fetched_at > datetime.now(UTC) - RETRY_MISS_AFTER)
    )
    if cached is not None and (cached.found or fresh_miss):
        return Lyrics(root, cached.synced, cached.plain, cached.source) if cached.found else None
    if not settings.lyrics_api_url:
        return None

    title = track.title or ""
    if not title_key(title):
        return None
    try:
        found = await fetch(
            http,
            settings.lyrics_api_url,
            await _artist_names(session, root),
            title,
            int(track.duration or 0),
        )
    except (httpx.HTTPError, ValueError) as exc:
        log.info("lyrics.fetch_failed", track_id=root, error=str(exc))
        return None

    row = cached or TrackLyrics(track_id=root, found=False)
    row.found = found is not None
    row.synced = (found or {}).get("syncedLyrics") or None
    row.plain = (found or {}).get("plainLyrics") or None
    row.source = "lrclib" if found else None
    row.fetched_at = datetime.now(UTC)
    if cached is None:
        session.add(row)
    if found is None:
        return None
    return Lyrics(root, row.synced, row.plain, row.source)
