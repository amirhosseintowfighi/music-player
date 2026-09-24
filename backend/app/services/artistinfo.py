"""Artist photos, from Spotify.

The catalogue learns an artist's name by parsing it out of a track title, which is
everything it knows about them: no photo, no popularity, nothing to build a page
around. Spotify knows all of it for anyone commercially released, so each artist is
matched once and the answer is kept.

Three things this is careful about:

- **It matches conservatively.** A wrong photo on an artist page is worse than no
  photo, so a result is only accepted when the normalised names agree. Persian names
  are searched by their Latin transliteration, which is what Spotify indexes.
- **It asks once.** ``enriched_at`` is set whether or not a match was found, so a
  name nobody released under is not looked up every hour forever.
- **It is optional.** With no credentials configured the job does nothing at all and
  the rest of the product does not notice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.text.finglish import skeleton
from app.domain.text.normalizer import normalize_key
from app.models import Artist
from tmusic_common.logging import get_logger

log = get_logger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 — a URL, not a secret
SEARCH_URL = "https://api.spotify.com/v1/search"
BATCH = 20
TIMEOUT_S = 10.0
# Spotify's token lasts an hour; re-asking a minute early avoids a race with expiry.
TOKEN_MARGIN_S = 60

_token: tuple[float, str] = (0.0, "")


@dataclass(frozen=True, slots=True)
class Profile:
    spotify_id: str
    image_url: str | None
    popularity: int | None


def configured(settings: Settings) -> bool:
    return bool(settings.spotify_client_id and settings.spotify_client_secret.get_secret_value())


def clear_token_cache() -> None:
    global _token
    _token = (0.0, "")


async def _access_token(http: httpx.AsyncClient, settings: Settings) -> str:
    """Client-credentials token, cached until it nearly expires."""
    global _token
    expires_at, value = _token
    if value and time.monotonic() < expires_at:
        return value
    response = await http.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(settings.spotify_client_id, settings.spotify_client_secret.get_secret_value()),
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    body = response.json()
    token = str(body.get("access_token", ""))
    lifetime = int(body.get("expires_in", 3600))
    _token = (time.monotonic() + max(lifetime - TOKEN_MARGIN_S, 60), token)
    return token


def matches(wanted: str, candidate: str) -> bool:
    """Is this the artist we asked about, or just someone with a similar record?

    Compared on the normalised name and on the Latin skeleton, so "Googoosh" matches
    "گوگوش" and "Moein" matches "معین" — but "Moein Zandi" does not match "Moein".
    """
    if normalize_key(wanted) == normalize_key(candidate):
        return True
    left, right = skeleton(wanted), skeleton(candidate)
    return bool(left) and left == right


async def lookup(
    http: httpx.AsyncClient, settings: Settings, name: str, latin_name: str | None
) -> Profile | None:
    """The best Spotify match for one artist, or None when nothing fits."""
    query = (latin_name or name).strip()
    if not query:
        return None
    token = await _access_token(http, settings)
    response = await http.get(
        SEARCH_URL,
        params={"q": query, "type": "artist", "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_S,
    )
    if response.status_code == 429:
        raise RateLimited(int(response.headers.get("Retry-After", "5")))
    response.raise_for_status()
    items = response.json().get("artists", {}).get("items", [])

    for item in items:
        candidate = str(item.get("name", ""))
        if not (matches(query, candidate) or matches(name, candidate)):
            continue
        images = item.get("images") or []
        # Spotify returns them largest first; the middle one is plenty for a header.
        picked = images[1] if len(images) > 1 else (images[0] if images else None)
        return Profile(
            spotify_id=str(item.get("id", "")),
            image_url=str(picked["url"]) if picked else None,
            popularity=item.get("popularity"),
        )
    return None


class RateLimited(Exception):
    """Spotify asked us to slow down. The batch stops; the rest keeps its turn."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"rate limited for {retry_after_s}s")
        self.retry_after_s = retry_after_s


async def candidates(session: AsyncSession, limit: int = BATCH) -> list[Artist]:
    """Never-looked-at artists, the ones with most tracks first."""
    rows = await session.scalars(
        select(Artist)
        .where(
            Artist.enriched_at.is_(None),
            Artist.merged_into_id.is_(None),
            Artist.hidden.is_(False),
        )
        .order_by(Artist.tracks_count.desc(), Artist.id)
        .limit(limit)
    )
    return list(rows.all())


async def enrich_batch(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = BATCH
) -> dict[str, int]:
    if not configured(settings):
        return {"looked_up": 0, "found": 0}

    found = 0
    looked_up = 0
    for artist in await candidates(session, limit):
        try:
            profile = await lookup(http, settings, artist.name, artist.latin_name)
        except RateLimited as exc:
            log.info("artistinfo.rate_limited", retry_after=exc.retry_after_s)
            break
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # A failed lookup is not an answer: leave enriched_at null and try later.
            log.info("artistinfo.failed", artist=artist.name, error=str(exc))
            continue
        looked_up += 1
        # Marked either way: "Spotify does not know this name" is an answer too.
        artist.enriched_at = datetime.now(UTC)
        if profile is not None:
            artist.spotify_id = profile.spotify_id
            artist.image_url = profile.image_url
            artist.popularity = profile.popularity
            found += 1
    if looked_up:
        log.info("artistinfo.enriched", looked_up=looked_up, found=found)
    return {"looked_up": looked_up, "found": found}


async def coverage(session: AsyncSession) -> dict[str, int]:
    """How complete the artist pages are, for the admin panel."""
    row = (
        await session.execute(
            text(
                """
        SELECT count(*) FILTER (WHERE merged_into_id IS NULL AND NOT hidden)        AS artists,
               count(*) FILTER (WHERE image_url IS NOT NULL)                        AS with_image,
               count(*) FILTER (WHERE enriched_at IS NULL AND merged_into_id IS NULL
                                  AND NOT hidden)                                   AS pending
          FROM artists
        """
            )
        )
    ).one()
    return {
        "artists": int(row.artists),
        "with_image": int(row.with_image),
        "pending": int(row.pending),
    }
