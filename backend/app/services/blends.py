"""Blend: one playlist made from two people's tastes.

One person sends an invite link; whoever opens it makes a Blend with them. Each of
the two gets their own copy of the playlist (so it sits in both libraries and either
can leave without taking it from the other), rebuilt from both histories daily.

The playlist opens with what they both love, then alternates between the two, so
each hears the other's favourites next to their own. The "taste match" is the
overlap of the artists they listen to, as a percentage.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime

from redis.asyncio import Redis
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInput, NotFound
from app.models import Blend, Playlist, User
from app.services import playlists

INVITE_TTL_S = 7 * 24 * 3600
BLEND_SIZE = 50
TOP_PER_USER = 150


def _invite_key(code: str) -> str:
    return f"blend:invite:{code}"


async def invite(redis: Redis, user_id: int) -> str:
    code = secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")
    await redis.set(_invite_key(code), str(user_id), ex=INVITE_TTL_S)
    return code


async def _top_tracks(session: AsyncSession, user_id: int) -> list[int]:
    """What this user plays and likes most, best first."""
    rows = await session.execute(
        text(
            """
        SELECT track_id FROM (
            SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id,
                   count(*) FILTER (WHERE h.completed) + 0.2 * count(*) AS score
              FROM play_history h JOIN tracks t ON t.id = h.track_id
             WHERE h.user_id = :uid AND h.played_at > now() - interval '120 days'
             GROUP BY 1
            UNION ALL
            SELECT coalesce(t.canonical_track_id, l.track_id), 5
              FROM likes l JOIN tracks t ON t.id = l.track_id
             WHERE l.user_id = :uid
        ) s
        JOIN tracks t ON t.id = s.track_id
        WHERE NOT t.hidden
          AND NOT EXISTS (SELECT 1 FROM hidden_tracks x
                           WHERE x.user_id = :uid AND x.track_id = s.track_id)
        GROUP BY track_id ORDER BY sum(score) DESC, track_id LIMIT :limit
        """
        ).bindparams(uid=user_id, limit=TOP_PER_USER)
    )
    return [row[0] for row in rows]


async def _artists_of(session: AsyncSession, track_ids: list[int]) -> set[int]:
    if not track_ids:
        return set()
    rows = await session.execute(
        text(
            "SELECT DISTINCT artist_id FROM track_artists"
            " WHERE track_id = ANY(:ids) AND role = 'primary'"
        ).bindparams(ids=track_ids)
    )
    return {row[0] for row in rows}


def mix(a: list[int], b: list[int], size: int = BLEND_SIZE) -> list[int]:
    """Shared favourites first, then one from each in turn."""
    in_b = set(b)
    both = [t for t in a if t in in_b]
    out = list(dict.fromkeys(both))
    seen = set(out)
    rest_a = [t for t in a if t not in seen]
    rest_b = [t for t in b if t not in seen]
    for pair in zip(rest_a, rest_b, strict=False):
        for track in pair:
            if track not in seen:
                out.append(track)
                seen.add(track)
    # One list ran out: keep going with the other.
    longer = rest_a if len(rest_a) > len(rest_b) else rest_b
    for track in longer:
        if track not in seen:
            out.append(track)
            seen.add(track)
    return out[:size]


def match_percent(artists_a: set[int], artists_b: set[int]) -> int:
    if not artists_a or not artists_b:
        return 0
    # Dice coefficient, eased upwards: two people rarely share more than half their
    # artists, and "23% match" reads as "we have nothing in common" when it is not.
    dice = 2 * len(artists_a & artists_b) / (len(artists_a) + len(artists_b))
    return min(100, round(100 * math.sqrt(dice)))


async def _name(session: AsyncSession, user_id: int) -> str:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    return user.first_name[:40]


async def _fill(
    session: AsyncSession, playlist_id: int | None, owner: int, name: str, ids: list[int]
) -> int:
    playlist = await session.get(Playlist, playlist_id) if playlist_id else None
    if playlist is None:
        playlist = Playlist(
            user_id=owner,
            name=name,
            description="Blend",
            kind="blend",
            generated_for=date.today(),
        )
        session.add(playlist)
        await session.flush()
    else:
        playlist.name = name
        playlist.generated_for = date.today()
        await session.execute(
            text("DELETE FROM playlist_tracks WHERE playlist_id = :id").bindparams(id=playlist.id)
        )
    if ids:
        await session.execute(
            text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position)"
                " VALUES (:pid, :tid, :pos) ON CONFLICT DO NOTHING"
            ),
            [{"pid": playlist.id, "tid": t, "pos": i * 1024} for i, t in enumerate(ids, 1)],
        )
    await playlists.refresh_counts(session, playlist.id)
    return int(playlist.id)


async def rebuild(session: AsyncSession, blend: Blend) -> Blend:
    top_a = await _top_tracks(session, blend.user_a)
    top_b = await _top_tracks(session, blend.user_b)
    ids = mix(top_a, top_b)
    blend.match_pct = match_percent(
        await _artists_of(session, top_a), await _artists_of(session, top_b)
    )
    name_a, name_b = await _name(session, blend.user_a), await _name(session, blend.user_b)
    blend.playlist_a = await _fill(
        session, blend.playlist_a, blend.user_a, f"{name_a} + {name_b}", ids
    )
    blend.playlist_b = await _fill(
        session, blend.playlist_b, blend.user_b, f"{name_b} + {name_a}", ids
    )
    blend.refreshed_at = datetime.now(UTC)
    return blend


async def join(session: AsyncSession, redis: Redis, code: str, user_id: int) -> Blend:
    inviter = await redis.get(_invite_key(code.strip()))
    if inviter is None:
        raise NotFound("this invite has expired")
    other = int(inviter)
    if other == user_id:
        raise InvalidInput("you cannot blend with yourself", reason="self")
    a, b = sorted((other, user_id))
    blend = (
        await session.scalars(select(Blend).where(Blend.user_a == a, Blend.user_b == b))
    ).one_or_none()
    if blend is None:
        blend = Blend(user_a=a, user_b=b)
        session.add(blend)
        await session.flush()
    return await rebuild(session, blend)


@dataclass(frozen=True, slots=True)
class BlendView:
    id: int
    other_user_id: int
    other_name: str
    playlist_id: int | None
    match_pct: int
    refreshed_at: datetime


async def list_for(session: AsyncSession, user_id: int) -> list[BlendView]:
    rows = await session.scalars(
        select(Blend)
        .where(or_(Blend.user_a == user_id, Blend.user_b == user_id))
        .order_by(Blend.created_at.desc())
    )
    out: list[BlendView] = []
    for blend in rows.all():
        mine_a = blend.user_a == user_id
        other = blend.user_b if mine_a else blend.user_a
        out.append(
            BlendView(
                id=blend.id,
                other_user_id=other,
                other_name=await _name(session, other),
                playlist_id=blend.playlist_a if mine_a else blend.playlist_b,
                match_pct=blend.match_pct,
                refreshed_at=blend.refreshed_at,
            )
        )
    return out


async def _mine(session: AsyncSession, blend_id: int, user_id: int) -> Blend:
    blend = await session.get(Blend, blend_id)
    if blend is None or user_id not in (blend.user_a, blend.user_b):
        raise NotFound("blend not found")
    return blend


async def refresh(session: AsyncSession, blend_id: int, user_id: int) -> Blend:
    return await rebuild(session, await _mine(session, blend_id, user_id))


async def leave(session: AsyncSession, blend_id: int, user_id: int) -> None:
    """Leaving ends the Blend for both; each copy of the playlist goes with it."""
    blend = await _mine(session, blend_id, user_id)
    for playlist_id in (blend.playlist_a, blend.playlist_b):
        if playlist_id:
            await session.execute(
                text("DELETE FROM playlists WHERE id = :id AND kind = 'blend'").bindparams(
                    id=playlist_id
                )
            )
    await session.delete(blend)


async def refresh_all(session: AsyncSession, limit: int = 2000) -> int:
    rows = await session.scalars(select(Blend).order_by(Blend.refreshed_at).limit(limit))
    count = 0
    for blend in rows.all():
        await rebuild(session, blend)
        count += 1
    return count
