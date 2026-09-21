"""Lazy file resolution (ADR-002, layer C).

The crawler builds the catalogue from public HTML, which knows a track's title,
artist and message id but not its file identity, duration or size. Those come from
one MTProto read of one message — so we do it at the moment somebody actually plays
the track, and never again. Playback is heavily Pareto: most of the catalogue is
never played, and paying an account request for it would be paying for nothing.

Two things this module is careful about:

- **It never becomes the thing that breaks playback.** A resolver account that is
  flooded or dead trips a circuit breaker; already-resolved tracks (which is most of
  what gets played) keep working untouched and the failure is logged, not raised at
  every user.
- **A resolve can discover the track is a duplicate.** The real ``file_unique_id``
  is the strongest identity we have, so the same file crawled from two channels
  collapses into one group here, through the same canonical machinery ingest uses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import NotFound
from app.models import Channel, IndexerAccount, Track
from app.services import ingest
from tmusic_common.indexer_contract import AccountReportIn, AudioItem, RegisterAccountsIn
from tmusic_common.logging import get_logger

log = get_logger(__name__)

# A play is waiting on this: long enough for one Telegram round-trip, no longer.
INLINE_TIMEOUT_S = 6.0
BACKGROUND_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3
# After this many consecutive failures the resolver is assumed down.
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN_S = 120.0

_SOURCE_SQL = text(
    """
    SELECT c.username, c.tg_channel_id, ct.message_id
    FROM channel_tracks ct
    JOIN channels c ON c.id = ct.channel_id
    WHERE ct.track_id = :id AND c.username IS NOT NULL
      AND c.status IN ('pending', 'indexing', 'active')
    ORDER BY ct.posted_at DESC
    LIMIT 1
    """
)


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0

    def open(self) -> bool:
        return time.monotonic() < self.open_until

    def trip(self, cooldown: float = BREAKER_COOLDOWN_S) -> None:
        self.failures += 1
        if self.failures >= BREAKER_THRESHOLD:
            self.open_until = time.monotonic() + cooldown
            log.warning("resolve.breaker_open", failures=self.failures, cooldown=cooldown)

    def ok(self) -> None:
        self.failures = 0
        self.open_until = 0.0


breaker = _Breaker()


def reset_breaker() -> None:
    """Test hook, and what the admin panel's "resolver is back" button will call."""
    breaker.ok()


async def resolve_track(
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    track_id: int,
    *,
    http_timeout: float = INLINE_TIMEOUT_S,
) -> Track | None:
    """Fills in a track's file identity. Returns the track when it is playable.

    Returns None rather than raising: every caller has something better to do than
    fail — playback says "not right now", the pre-warm job moves to the next track.
    """
    track = await session.get(Track, track_id)
    if track is None:
        return None
    if track.resolve_status == "resolved":
        return track
    if not settings.edge_internal_url or breaker.open():
        return None
    if track.resolve_attempts >= MAX_ATTEMPTS:
        return None

    row = (await session.execute(_SOURCE_SQL.bindparams(id=track_id))).one_or_none()
    if row is None:
        track.resolve_status = "failed"
        await session.commit()
        return None

    # Committed before the call, and again after it: a refused playback raises, and the
    # request's transaction is rolled back with it. Without its own commit the attempt
    # count would vanish and a permanently broken track would be retried on every play.
    track.resolve_attempts += 1
    track.resolve_status = "pending"
    await session.commit()

    try:
        response = await http.post(
            f"{settings.edge_internal_url.rstrip('/')}/internal/resolve",
            json={
                "channel_username": row.username,
                "channel_id": row.tg_channel_id,
                "message_id": row.message_id,
            },
            headers={"Authorization": f"Bearer {settings.internal_api_token.get_secret_value()}"},
            timeout=http_timeout,
        )
    except httpx.HTTPError as exc:
        breaker.trip()
        log.info("resolve.edge_unreachable", track_id=track_id, error=str(exc))
        track.resolve_status = "unresolved"
        await session.commit()
        return None

    if response.status_code != 200:
        # 503 means the account is flooded, not that the track is bad: do not burn
        # an attempt on it, just stop asking for a while.
        if response.status_code == 503:
            track.resolve_attempts -= 1
            breaker.trip(float(response.headers.get("Retry-After", BREAKER_COOLDOWN_S)))
        else:
            breaker.trip()
        track.resolve_status = "failed" if track.resolve_attempts >= MAX_ATTEMPTS else "unresolved"
        log.info("resolve.failed", track_id=track_id, status=response.status_code)
        await session.commit()
        return None

    breaker.ok()
    return await apply_resolved(session, track, AudioItem.model_validate(response.json()))


async def apply_resolved(session: AsyncSession, track: Track, item: AudioItem) -> Track:
    """Writes what MTProto told us, then folds the track into its duplicate group."""
    assert item.file_unique_id is not None
    twin = (
        await session.scalars(
            select(Track).where(Track.file_unique_id == item.file_unique_id, Track.id != track.id)
        )
    ).one_or_none()

    track.duration = item.duration
    track.file_size = item.file_size
    track.mime_type = item.mime_type or track.mime_type
    track.has_thumb = item.has_thumb
    track.file_name = item.file_name or track.file_name
    track.resolve_status = "resolved"
    track.resolved_at = datetime.now(UTC)

    if twin is not None:
        # The same file, already known under another row: this one is a duplicate and
        # keeps its source_key as identity. Merging is what the file id is *for*.
        root = twin.canonical_track_id or twin.id
        track.canonical_track_id = root
        if twin.hidden:
            track.hidden, track.hidden_reason = True, "duplicate_of_hidden"
        log.info("resolve.merged", track_id=track.id, into=root, fuid=item.file_unique_id)
    else:
        track.file_unique_id = item.file_unique_id

    await session.flush()
    if track.canonical_track_id is None:
        # Now that the duration is real, the usual fuzzy dedup can see this track.
        await ingest.dedup(session, [track.id])
    await ingest.refresh_group_counts(session, [track.id])
    return track


async def record_demand(session: AsyncSession, track_id: int) -> None:
    """Somebody wanted this track and could not have it.

    With no CDN fallback (ADR-002 §8) this is the only signal that pre-warm picked the
    wrong tracks, so it is recorded even though the request itself failed — in its own
    transaction, because the caller is about to raise.
    """
    await session.execute(
        text("UPDATE tracks SET resolve_requests = resolve_requests + 1 WHERE id = :id").bindparams(
            id=track_id
        )
    )
    await session.commit()


# Pre-warm is not a background nicety any more (ADR-002, decision of 2026-09-20): it is
# the thing that keeps users off the inline path. Order of attention:
#   1. what somebody already asked for and did not get
#   2. what is liked, played, or sitting in a playlist
#   3. everything in a featured channel — the shelf we put in front of new users
_PREWARM_SQL = text(
    """
    SELECT t.id
    FROM tracks t
    WHERE t.resolve_status = 'unresolved'
      AND t.resolve_attempts < :max_attempts
      AND NOT t.hidden AND t.playable
    ORDER BY
      t.resolve_requests DESC,
      (t.likes_count * 3 + t.plays_7d
         + CASE WHEN EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id)
                THEN 5 ELSE 0 END
         + CASE WHEN EXISTS (
                  SELECT 1 FROM channel_tracks ct
                    JOIN channels c ON c.id = ct.channel_id AND c.is_featured
                   WHERE ct.track_id = t.id)
                THEN 3 ELSE 0 END) DESC,
      t.id
    LIMIT :limit
    """
)


async def prewarm(
    session: AsyncSession, http: httpx.AsyncClient, settings: Settings, limit: int = 25
) -> dict[str, int]:
    """Resolves the tracks most likely to be played next, before anyone waits on it.

    Low priority by construction: a small batch, only what is already popular, and it
    stops the moment the breaker says the account is unhappy.
    """
    if not settings.edge_internal_url or breaker.open():
        return {"resolved": 0, "failed": 0}
    ids = list(
        await session.scalars(_PREWARM_SQL.bindparams(limit=limit, max_attempts=MAX_ATTEMPTS))
    )
    resolved = failed = 0
    for track_id in ids:
        if breaker.open():
            break
        track = await resolve_track(
            session, http, settings, track_id, http_timeout=BACKGROUND_TIMEOUT_S
        )
        if track is not None and track.resolve_status == "resolved":
            resolved += 1
        else:
            failed += 1
    log.info("resolve.prewarmed", resolved=resolved, failed=failed)
    return {"resolved": resolved, "failed": failed}


async def stats(session: AsyncSession) -> dict[str, int | bool]:
    """Resolver status for the admin panel (phase 5) and the health alert."""
    row = (
        await session.execute(
            text(
                """
        SELECT count(*) FILTER (WHERE resolve_status = 'unresolved') AS unresolved,
               count(*) FILTER (WHERE resolve_status = 'pending')    AS pending,
               count(*) FILTER (WHERE resolve_status = 'failed')     AS failed,
               count(*) FILTER (WHERE resolve_status = 'resolved')   AS resolved
          FROM tracks
        """
            )
        )
    ).one()
    return {
        "unresolved": int(row.unresolved),
        "pending": int(row.pending),
        "failed": int(row.failed),
        "resolved": int(row.resolved),
        "breaker_open": breaker.open(),
        "consecutive_failures": breaker.failures,
    }


# ── the resolver's Telegram account ───────────────────────────────────────────
#
# One account, announced by the edge on startup so the panel can show its state.
# The pool, its round-robin and its health check are gone with ADR-002 §6.


async def register_accounts(session: AsyncSession, body: RegisterAccountsIn) -> dict[str, int]:
    if not body.accounts:
        return {}
    stmt = (
        insert(IndexerAccount)
        .values([{"session_key": a.session_key, "phone_hint": a.phone_hint} for a in body.accounts])
        .on_conflict_do_update(
            index_elements=[IndexerAccount.session_key],
            set_={"last_seen_at": func.now()},
        )
        .returning(IndexerAccount.session_key, IndexerAccount.id)
    )
    return {key: aid for key, aid in (await session.execute(stmt)).tuples()}


async def report_account(session: AsyncSession, account_id: int, body: AccountReportIn) -> None:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "status": body.status,
        "cooling_until": body.cooling_until,
        "last_seen_at": now,
        "floodwait_24h_s": IndexerAccount.floodwait_24h_s + body.floodwait_s,
    }
    if body.error:
        values["last_error"] = body.error
    if body.status == "healthy":
        values["last_ok_at"] = now
    result = await session.execute(
        update(IndexerAccount)
        .where(IndexerAccount.id == account_id)
        .values(**values)
        .returning(IndexerAccount.id)
    )
    if result.first() is None:
        raise NotFound("account not found")
    if body.status in ("limited", "banned"):
        log.warning("indexer.account_degraded", account_id=account_id, status=body.status)
    if body.status == "banned":
        # Nothing to hand over: the catalogue does not depend on this account, only
        # resolving does, and the core's circuit breaker already covers that.
        await session.execute(
            update(Channel)
            .where(Channel.indexer_account_id == account_id)
            .values(indexer_account_id=None)
        )
