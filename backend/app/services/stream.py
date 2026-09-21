"""Issues signed stream tickets (ADR-0004). Bytes never pass through the core."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.config import Settings
from app.errors import LimitReached, NotFound, Unavailable
from app.models import EdgeNode, Track
from app.redis_util import resolve
from app.schemas import StreamOut
from app.security.tokens import AccessClaims
from app.services import plans, resolving
from tmusic_common.stream_ticket import StreamTicket, sign

EDGE_CACHE_TTL_S = 30
THUMB_TICKET_TTL_S = 3600
DEFAULT_MIME = "audio/mpeg"

_edges: tuple[float, list[tuple[str, int]]] = (0.0, [])


@dataclass(frozen=True, slots=True)
class Source:
    track_id: int
    row_id: int
    resolved: bool
    file_size: int
    mime: str | None
    has_thumb: bool
    bot_file_id: str | None
    channel_id: int | None
    channel_username: str | None
    message_id: int | None


def edge_base_url(host: str) -> str:
    """``edge_nodes.host`` is a bare host (https implied) or a full base URL (local dev)."""
    return host.rstrip("/") if "://" in host else f"https://{host}"


def clear_edge_cache() -> None:
    global _edges
    _edges = (0.0, [])


async def pick_edge(session: AsyncSession) -> str:
    global _edges
    loaded_at, nodes = _edges
    if time.monotonic() - loaded_at > EDGE_CACHE_TTL_S:
        rows = await session.execute(
            select(EdgeNode.host, EdgeNode.weight).where(
                EdgeNode.is_enabled, EdgeNode.healthy, EdgeNode.weight > 0
            )
        )
        nodes = [(h, w) for h, w in rows.tuples()]
        _edges = (time.monotonic(), nodes)
    if not nodes:
        raise Unavailable("no streaming edge available")
    hosts = [h for h, _ in nodes]
    weights = [w for _, w in nodes]
    # Load balancing, not cryptography.
    host: str = random.choices(hosts, weights=weights, k=1)[0]  # noqa: S311
    return host


_SOURCES_SQL = text(
    """
    SELECT g.id, g.bot_file_id, g.bot_id, g.file_size, g.mime_type, g.has_thumb,
           g.resolve_status, src.tg_channel_id, src.username, src.message_id
    FROM tracks g
    LEFT JOIN LATERAL (
        SELECT c.tg_channel_id, c.username, ct.message_id
        FROM channel_tracks ct
        JOIN channels c ON c.id = ct.channel_id
        WHERE ct.track_id = g.id
          AND c.username IS NOT NULL
          AND c.status IN ('indexing', 'active')
        ORDER BY ct.posted_at DESC
        LIMIT 1
    ) src ON true
    WHERE (g.id = :root OR g.canonical_track_id = :root)
      AND g.playable AND NOT g.hidden
    ORDER BY (g.id = :root) DESC, g.id
    """
)


async def pick_source(session: AsyncSession, track_id: int, bot_id: int, bot_max: int) -> Source:
    track = await session.get(Track, track_id)
    if track is None or track.hidden:
        raise NotFound("track not found")
    root = track.canonical_track_id or track.id
    rows = (await session.execute(_SOURCES_SQL.bindparams(root=root))).all()
    # The criterion is "has our bot seen this file?", not "is the track resolved?":
    # a bot file id is issued per bot and the resolver never produces one (ADR-003).
    bot_ok = [r for r in rows if r.bot_file_id and r.bot_id == bot_id and r.file_size <= bot_max]
    mtproto_ok = [r for r in rows if r.username and r.message_id is not None]
    # A resolved row knows its real size, so it can be streamed without asking Telegram
    # anything first; an unresolved one is only a candidate if nothing better exists.
    mtproto_ok.sort(key=lambda r: r.resolve_status != "resolved")
    # Bot API first (no user-account quota), then MTProto (ADR-0004).
    candidates = bot_ok or mtproto_ok
    if not candidates:
        raise Unavailable("track is not playable right now", reason="no_source")
    best = candidates[0]
    via_bot = bool(bot_ok)
    return Source(
        track_id=root,
        row_id=best.id,
        resolved=via_bot or best.resolve_status == "resolved",
        file_size=best.file_size,
        mime=best.mime_type,
        has_thumb=best.has_thumb,
        bot_file_id=best.bot_file_id if via_bot else None,
        channel_id=best.tg_channel_id if not via_bot else None,
        channel_username=best.username if not via_bot else None,
        message_id=best.message_id if not via_bot else None,
    )


async def _count_play(redis: Redis, user_id: int, track_id: int, limit: int) -> None:
    """Free-plan daily cap, counted as distinct tracks per UTC day."""
    if limit < 0:
        return
    key = f"plays:{user_id}:{datetime.now(UTC):%Y%m%d}"
    added = await resolve(redis.sadd(key, str(track_id)))
    if added:
        await resolve(redis.expire(key, 2 * 86400))
        if await resolve(redis.scard(key)) > limit:
            await resolve(redis.srem(key, str(track_id)))
            raise LimitReached("daily play limit reached", kind="daily_plays", limit=limit)


# A prefetch ticket may only pull the head of the file — enough for the player to
# start instantly on the next track, never enough to stand in for a real play.
PREFETCH_BYTES = 512 * 1024


def build_ticket(source: Source, user_id: int, ttl_s: int, *, max_bytes: int = 0) -> StreamTicket:
    return StreamTicket(
        track_id=source.track_id,
        user_id=user_id,
        exp=int(time.time()) + ttl_s,
        size=source.file_size,
        mime=source.mime or DEFAULT_MIME,
        channel_id=source.channel_id,
        channel_username=source.channel_username,
        message_id=source.message_id,
        bot_file_id=source.bot_file_id,
        max_bytes=max_bytes,
    )


async def issue_ticket(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    claims: AccessClaims,
    track_id: int,
    http: httpx.AsyncClient | None = None,
    *,
    prefetch: bool = False,
) -> StreamOut:
    """A signed URL for one track.

    ``prefetch`` is the player warming up the *next* track: it does not count against
    the daily limit and the ticket it returns is capped to the first few hundred
    kilobytes. When the track is actually played, the client asks again without the
    flag and gets a full, counted ticket (ADR-003 §2-3).
    """
    source = await pick_source(session, track_id, settings.bot_id, settings.bot_api_max_download)
    if not source.resolved and http is not None:
        # A crawled track nobody has played yet: the catalogue knows the message, not
        # the file. One MTProto read fills that in, once, and the answer is kept
        # forever (ADR-002 §8 — the preview has no CDN link to fall back on).
        metrics.PLAYBACK_INLINE_RESOLVE.labels("attempted").inc()
        resolved = await resolving.resolve_track(session, http, settings, source.row_id)
        if resolved is None or resolved.resolve_status != "resolved":
            # No CDN to fall back on (ADR-002 §8): record the demand so pre-warm goes
            # after what people actually want, and tell the client plainly.
            metrics.PLAYBACK_INLINE_RESOLVE.labels("failed").inc()
            await resolving.record_demand(session, source.row_id)
            raise Unavailable("track is not playable right now", reason="resolving")
        metrics.PLAYBACK_INLINE_RESOLVE.labels("resolved").inc()
        source = await pick_source(
            session, track_id, settings.bot_id, settings.bot_api_max_download
        )
    if not prefetch:
        plan = await plans.get_plan(session, claims.plan)
        await _count_play(redis, claims.user_id, source.track_id, plan.limit("daily_plays"))
    metrics.PLAYBACK_TICKETS.labels("prefetch" if prefetch else "play").inc()
    host = await pick_edge(session)
    ticket = build_ticket(
        source,
        claims.user_id,
        settings.stream_ticket_ttl_s,
        max_bytes=PREFETCH_BYTES if prefetch else 0,
    )
    exp = ticket.exp
    token = sign(ticket, settings.signing_keys[0])
    base = edge_base_url(host)
    return StreamOut(
        url=f"{base}/s/{source.track_id}?t={token}",
        thumb_url=f"{base}/t/{source.track_id}?t={token}" if source.has_thumb else None,
        expires_at=exp,
        size=source.file_size,
        mime=ticket.mime,
    )


_THUMBS_SQL = text(
    """
    WITH roots AS (
        SELECT t.id AS asked, COALESCE(t.canonical_track_id, t.id) AS root
        FROM tracks t WHERE t.id = ANY(:ids) AND NOT t.hidden
    ), best AS (
        SELECT DISTINCT ON (r.root)
               r.root, g.file_size, g.mime_type, c.tg_channel_id, c.username, ct.message_id
        FROM roots r
        JOIN tracks g ON (g.id = r.root OR g.canonical_track_id = r.root)
                     AND g.playable AND NOT g.hidden AND g.has_thumb
        JOIN channel_tracks ct ON ct.track_id = g.id
        JOIN channels c ON c.id = ct.channel_id
                       AND c.username IS NOT NULL
                       AND c.status IN ('indexing', 'active')
        ORDER BY r.root, ct.posted_at DESC
    )
    SELECT r.asked, b.root, b.file_size, b.mime_type, b.tg_channel_id, b.username, b.message_id
    FROM roots r JOIN best b ON b.root = r.root
    """
)


async def thumbnail_urls(
    session: AsyncSession, settings: Settings, user_id: int, track_ids: list[int]
) -> dict[str, str]:
    """Signed artwork URLs for a batch of tracks.

    Artwork must be cheap: this issues longer-lived tickets in one query and never
    touches the daily play limit (browsing a list is not listening).
    """
    if not track_ids:
        return {}
    rows = (await session.execute(_THUMBS_SQL.bindparams(ids=track_ids))).all()
    if not rows:
        return {}
    host = await pick_edge(session)
    base = edge_base_url(host)
    exp = int(time.time()) + THUMB_TICKET_TTL_S
    out: dict[str, str] = {}
    signed: dict[int, str] = {}
    for row in rows:
        token = signed.get(row.root)
        if token is None:
            token = sign(
                StreamTicket(
                    track_id=row.root,
                    user_id=user_id,
                    exp=exp,
                    size=row.file_size,
                    mime=row.mime_type or DEFAULT_MIME,
                    channel_id=row.tg_channel_id,
                    channel_username=row.username,
                    message_id=row.message_id,
                ),
                settings.signing_keys[0],
            )
            signed[row.root] = token
        out[str(row.asked)] = f"{base}/t/{row.root}?t={token}"
    return out
