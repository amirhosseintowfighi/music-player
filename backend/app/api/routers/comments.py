"""Comments and reactions on a song (0022)."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Query

from app.api.deps import Claims, SessionDep, WritableClaims
from app.schemas import (
    CommentAuthor,
    CommentIn,
    CommentOut,
    CommentPageOut,
    Reaction,
    ReactionsOut,
)
from app.services import comments

router = APIRouter(prefix="/v1", tags=["comments"])


def _out(row: comments.CommentRow, viewer: int) -> CommentOut:
    return CommentOut(
        id=row.id,
        author=CommentAuthor(id=row.user_id, first_name=row.first_name, username=row.username),
        body=row.body,
        at_second=row.at_second,
        created_at=row.created_at,
        mine=row.user_id == viewer,
    )


def _reactions(found: comments.Reactions) -> ReactionsOut:
    return ReactionsOut(counts=found.counts, mine=cast(Any, found.mine))


@router.get("/tracks/{track_id}/comments", response_model=CommentPageOut)
async def track_comments(
    track_id: int,
    claims: Claims,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommentPageOut:
    rows, next_cursor, total = await comments.page(session, track_id, cursor, limit)
    return CommentPageOut(
        items=[_out(r, claims.user_id) for r in rows], next_cursor=next_cursor, total=total
    )


@router.post("/tracks/{track_id}/comments", response_model=CommentOut, status_code=201)
async def add_comment(
    track_id: int, body: CommentIn, claims: WritableClaims, session: SessionDep
) -> CommentOut:
    """``no_links`` refuses links and @handles; at most ten comments in ten minutes."""
    row = await comments.add(session, claims.user_id, track_id, body.body, body.at_second)
    return _out(row, claims.user_id)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: int, claims: WritableClaims, session: SessionDep) -> None:
    await comments.remove(session, claims.user_id, comment_id)


@router.get("/tracks/{track_id}/reactions", response_model=ReactionsOut)
async def track_reactions(track_id: int, claims: Claims, session: SessionDep) -> ReactionsOut:
    return _reactions(await comments.reactions(session, claims.user_id, track_id))


@router.put("/tracks/{track_id}/reactions/{emoji}", response_model=ReactionsOut)
async def add_reaction(
    track_id: int, emoji: Reaction, claims: WritableClaims, session: SessionDep
) -> ReactionsOut:
    return _reactions(await comments.react(session, claims.user_id, track_id, emoji, True))


@router.delete("/tracks/{track_id}/reactions/{emoji}", response_model=ReactionsOut)
async def remove_reaction(
    track_id: int, emoji: Reaction, claims: WritableClaims, session: SessionDep
) -> ReactionsOut:
    return _reactions(await comments.react(session, claims.user_id, track_id, emoji, False))
