"""Comments and reactions on a song (0022).

Both belong to the *root* track, so every copy of a song shares one conversation.
A comment can point at a second in the song. Links are refused (the cheapest spam
filter there is), people can post at most ``BURST`` comments per ``BURST_WINDOW``,
and authors can delete their own. Moderators hide rather than delete, so a report
can still be looked into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidInput, NotFound, RateLimited
from app.models import Track, TrackComment, TrackReaction
from app.services.history import canonical_id
from app.services.pagination import cursor_datetime, cursor_int, decode_cursor, encode_cursor

REACTIONS = ("fire", "heart", "cry", "love", "party", "clap")
MAX_BODY = 500
BURST = 10
BURST_WINDOW = timedelta(minutes=10)
_LINK = re.compile(r"(https?://|www\.|t\.me/|@\w{4,})", re.IGNORECASE)


async def _root(session: AsyncSession, track_id: int) -> int:
    exists = await session.scalar(
        select(Track.id).where(Track.id == track_id, Track.hidden.is_(False))
    )
    if exists is None:
        raise NotFound("track not found")
    return await canonical_id(session, track_id)


def clean(body: str) -> str:
    cleaned = " ".join(body.split())
    if not cleaned:
        raise InvalidInput("empty comment", reason="empty")
    if _LINK.search(cleaned):
        raise InvalidInput("links are not allowed in comments", reason="no_links")
    return cleaned[:MAX_BODY]


@dataclass(frozen=True, slots=True)
class CommentRow:
    id: int
    user_id: int
    first_name: str
    username: str | None
    body: str
    at_second: int | None
    created_at: datetime


async def add(
    session: AsyncSession, user_id: int, track_id: int, body: str, at_second: int | None
) -> CommentRow:
    root = await _root(session, track_id)
    cleaned = clean(body)
    recent = await session.scalar(
        select(func.count())
        .select_from(TrackComment)
        .where(
            TrackComment.user_id == user_id,
            TrackComment.created_at > func.now() - BURST_WINDOW,
        )
    )
    if (recent or 0) >= BURST:
        raise RateLimited("too many comments, slow down")
    if at_second is not None:
        duration = await session.scalar(select(Track.duration).where(Track.id == root))
        if duration and at_second > duration:
            at_second = duration
    comment = TrackComment(track_id=root, user_id=user_id, body=cleaned, at_second=at_second)
    session.add(comment)
    await session.flush()
    rows = await _rows(session, "c.id = :cid", {"cid": comment.id}, 1)
    return rows[0]


async def _rows(
    session: AsyncSession, where: str, params: dict[str, object], limit: int
) -> list[CommentRow]:
    rows = await session.execute(
        text(
            "SELECT c.id, c.user_id, u.first_name, u.username, c.body, c.at_second, c.created_at "
            "FROM track_comments c JOIN users u ON u.id = c.user_id "
            "WHERE NOT c.hidden AND NOT u.is_banned AND "
            + where
            + " ORDER BY c.created_at DESC, c.id DESC LIMIT :lim"
        ).bindparams(**params, lim=limit)
    )
    return [
        CommentRow(
            id=int(r.id),
            user_id=int(r.user_id),
            first_name=r.first_name or "",
            username=r.username,
            body=r.body,
            at_second=r.at_second,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def page(
    session: AsyncSession, track_id: int, cursor: str | None, limit: int
) -> tuple[list[CommentRow], str | None, int]:
    """Newest first, keyset-paged. Also returns how many comments the song has."""
    root = await _root(session, track_id)
    where = "c.track_id = :tid"
    params: dict[str, object] = {"tid": root}
    after = decode_cursor(cursor, 2)
    if after is not None:
        where += " AND (c.created_at, c.id) < (:c_at, :c_id)"
        params |= {"c_at": cursor_datetime(after[0]), "c_id": cursor_int(after[1])}
    rows = await _rows(session, where, params, limit + 1)
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    total = await session.scalar(
        select(func.count())
        .select_from(TrackComment)
        .where(TrackComment.track_id == root, TrackComment.hidden.is_(False))
    )
    return rows, next_cursor, int(total or 0)


async def remove(session: AsyncSession, user_id: int, comment_id: int) -> None:
    comment = await session.get(TrackComment, comment_id)
    if comment is None or comment.hidden:
        raise NotFound("comment not found")
    if comment.user_id != user_id:
        raise Forbidden("only the author can delete a comment")
    await session.delete(comment)


async def recent_for_review(session: AsyncSession, limit: int = 50) -> list[dict[str, object]]:
    """The newest comments with their song, for moderators to skim."""
    rows = await session.execute(
        text(
            "SELECT c.id, c.body, c.created_at, c.track_id, t.title, c.user_id, u.first_name "
            "FROM track_comments c JOIN users u ON u.id = c.user_id "
            "JOIN tracks t ON t.id = c.track_id WHERE NOT c.hidden "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT :lim"
        ).bindparams(lim=limit)
    )
    return [dict(row._mapping) for row in rows]


async def hide(session: AsyncSession, comment_id: int) -> None:
    comment = await session.get(TrackComment, comment_id)
    if comment is None:
        raise NotFound("comment not found")
    comment.hidden = True


@dataclass(frozen=True, slots=True)
class Reactions:
    counts: dict[str, int]
    mine: list[str]


async def reactions(session: AsyncSession, user_id: int, track_id: int) -> Reactions:
    root = await _root(session, track_id)
    rows = await session.execute(
        text(
            "SELECT emoji, count(*) AS n, bool_or(user_id = :uid) AS mine "
            "FROM track_reactions WHERE track_id = :tid GROUP BY emoji"
        ).bindparams(tid=root, uid=user_id)
    )
    counts = dict.fromkeys(REACTIONS, 0)
    mine = []
    for row in rows:
        counts[row.emoji] = int(row.n)
        if row.mine:
            mine.append(row.emoji)
    return Reactions(counts=counts, mine=[e for e in REACTIONS if e in mine])


async def react(
    session: AsyncSession, user_id: int, track_id: int, emoji: str, on: bool
) -> Reactions:
    if emoji not in REACTIONS:
        raise InvalidInput("unknown reaction", reason="bad_reaction")
    root = await _root(session, track_id)
    if on:
        await session.execute(
            insert(TrackReaction)
            .values(track_id=root, user_id=user_id, emoji=emoji)
            .on_conflict_do_nothing()
        )
    else:
        await session.execute(
            delete(TrackReaction).where(
                TrackReaction.track_id == root,
                TrackReaction.user_id == user_id,
                TrackReaction.emoji == emoji,
            )
        )
    return await reactions(session, user_id, track_id)
