"""Last.fm: the order of an artist's best-known songs, for "This Is" playlists.

Only the *order* comes from Last.fm — which songs of an artist people actually
listen to, worldwide. Every track played is our own: the titles are matched against
this artist's tracks in the archive, and anything Last.fm lists that we do not have
is simply left out.

Like the Spotify photos (``artistinfo``), it is optional and asks once: with no API
key the playlists are ordered by what people play here, and an artist Last.fm does
not know is remembered as such rather than asked about again every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.text.finglish import skeleton
from app.domain.text.normalizer import normalize_key
from app.errors import NotFound
from app.models import Artist
from tmusic_common.logging import get_logger

log = get_logger(__name__)

API_URL = "https://ws.audioscrobbler.com/2.0/"
TIMEOUT_S = 8.0
TOP_LIMIT = 50
BATCH = 20
# How long an answer is trusted. An artist's top songs move slowly.
REFRESH_AFTER = timedelta(days=30)
THIS_IS_SIZE = 50
# Last.fm's "artist not found".
_NOT_FOUND = 6

# "(Live)", "[Remastered 2011]", "- Radio Edit", "feat. X": the same song to a listener.
_DECORATION = re.compile(r"\s*[\(\[][^)\]]*[\)\]]|\s+-\s+.*$|\s+(?:feat|ft)\.?\s.*$", re.IGNORECASE)


def configured(settings: Settings) -> bool:
    return bool(settings.lastfm_api_key.get_secret_value())


def title_key(title: str) -> str:
    """What two spellings of one song title have in common."""
    return normalize_key(_DECORATION.sub("", title or "")) or normalize_key(title)


async def top_titles(
    http: httpx.AsyncClient, settings: Settings, name: str, latin_name: str | None
) -> list[str]:
    """The artist's most-played song titles on Last.fm, best first.

    An empty list is an answer ("Last.fm does not know them"); a network or server
    failure raises, so the caller can try again later.
    """
    for query in dict.fromkeys(q.strip() for q in (latin_name or "", name) if q and q.strip()):
        response = await http.get(
            API_URL,
            params={
                "method": "artist.gettoptracks",
                "artist": query,
                "autocorrect": 1,
                "limit": TOP_LIMIT,
                "api_key": settings.lastfm_api_key.get_secret_value(),
                "format": "json",
            },
            timeout=TIMEOUT_S,
        )
        body = response.json()
        if isinstance(body, dict) and body.get("error") == _NOT_FOUND:
            continue
        response.raise_for_status()
        tracks = body.get("toptracks", {}).get("track", [])
        titles = [str(t.get("name", "")) for t in tracks if t.get("name")]
        if titles:
            return titles
    return []


async def refresh_artist(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, artist: Artist
) -> None:
    artist.lastfm_top = await top_titles(http, settings, artist.name, artist.latin_name)
    artist.lastfm_fetched_at = datetime.now(UTC)


async def enrich_batch(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = BATCH
) -> dict[str, int]:
    """Looks up the artists people are most likely to open, a few at a time."""
    if not configured(settings):
        return {"looked_up": 0, "found": 0}
    stale = datetime.now(UTC) - REFRESH_AFTER
    rows = await session.scalars(
        select(Artist)
        .where(
            Artist.merged_into_id.is_(None),
            Artist.hidden.is_(False),
            Artist.tracks_count > 0,
            or_(Artist.lastfm_fetched_at.is_(None), Artist.lastfm_fetched_at < stale),
        )
        .order_by(Artist.lastfm_fetched_at.asc().nulls_first(), Artist.tracks_count.desc())
        .limit(limit)
    )
    looked_up = found = 0
    for artist in rows.all():
        try:
            await refresh_artist(session, http, settings, artist)
        except (httpx.HTTPError, ValueError, KeyError, AttributeError) as exc:
            log.info("lastfm.failed", artist=artist.name, error=str(exc))
            continue
        looked_up += 1
        found += bool(artist.lastfm_top)
    if looked_up:
        log.info("lastfm.enriched", looked_up=looked_up, found=found)
    return {"looked_up": looked_up, "found": found}


@dataclass(frozen=True, slots=True)
class ThisIs:
    artist_id: int
    track_ids: list[int]
    # "lastfm" when the order is Last.fm's, "plays" when it is our own listeners'.
    source: str


async def this_is(
    session: AsyncSession,
    artist_id: int,
    http: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    size: int = THIS_IS_SIZE,
) -> ThisIs:
    """An artist's essentials: their best-known songs that we have, best first."""
    artist = await session.get(Artist, artist_id)
    if artist is None or artist.hidden:
        raise NotFound("artist not found")
    if artist.merged_into_id is not None:
        return await this_is(session, artist.merged_into_id, http, settings, size)

    # Never asked about, and we can ask: do it now rather than show a worse order.
    if (
        artist.lastfm_fetched_at is None
        and http is not None
        and settings is not None
        and configured(settings)
    ):
        try:
            await refresh_artist(session, http, settings, artist)
        except (httpx.HTTPError, ValueError, KeyError, AttributeError) as exc:
            log.info("lastfm.inline_failed", artist=artist.name, error=str(exc))

    rows = (
        await session.execute(
            text(
                """
        SELECT t.id, t.title
          FROM track_artists ta
          JOIN tracks t ON t.id = ta.track_id
         WHERE ta.artist_id = :aid AND t.canonical_track_id IS NULL AND NOT t.hidden
         ORDER BY t.likes_count DESC, t.plays_total DESC, t.channels_count DESC, t.id
        """
            ).bindparams(aid=artist.id)
        )
    ).all()
    ours = [(row.id, title_key(row.title)) for row in rows]

    chosen: list[int] = []
    taken: set[int] = set()
    for wanted in artist.lastfm_top or []:
        key = title_key(wanted)
        loose = skeleton(wanted)
        for track_id, have in ours:
            if track_id in taken:
                continue
            # The skeleton lets "Gole Sangam" find «گل سنگم»; too short a skeleton
            # matches anything, so it only counts from four letters up.
            if have == key or (len(loose) >= 4 and skeleton(have) == loose):
                chosen.append(track_id)
                taken.add(track_id)
                break
    source = "lastfm" if chosen else "plays"
    # Fill the rest with what our own listeners like, so the playlist is never short.
    for track_id, _ in ours:
        if len(chosen) >= size:
            break
        if track_id not in taken:
            chosen.append(track_id)
            taken.add(track_id)
    return ThisIs(artist_id=artist.id, track_ids=chosen[:size], source=source)
