from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import Claims, SessionDep, WritableClaims
from app.models import Channel
from app.schemas import (
    AddChannelIn,
    AddChannelOut,
    CategoryOut,
    ChannelOut,
    Page,
    SuggestChannelOut,
    UserChannelOut,
)
from app.services import channels, discovery
from app.services.pagination import cursor_int, decode_cursor, encode_cursor

router = APIRouter(prefix="/v1", tags=["channels"])


def _out(c: Channel) -> ChannelOut:
    return ChannelOut.model_validate(c, from_attributes=True)


@router.get("/library/channels", response_model=list[UserChannelOut])
async def my_channels(claims: Claims, session: SessionDep) -> list[UserChannelOut]:
    rows = await channels.list_user_channels(session, claims.user_id)
    return [UserChannelOut(**_out(c).model_dump(), added_at=uc.added_at) for c, uc in rows]


@router.post("/library/channels", response_model=AddChannelOut, status_code=201)
async def add_channel(
    body: AddChannelIn, claims: WritableClaims, session: SessionDep
) -> AddChannelOut:
    ref = channels.parse_channel_ref(body.ref)
    channel, created = await channels.subscribe(session, claims.user_id, ref)
    return AddChannelOut(channel=_out(channel), created=created)


@router.post("/channels/suggest", response_model=SuggestChannelOut)
async def suggest_channel(
    body: AddChannelIn, claims: WritableClaims, session: SessionDep
) -> SuggestChannelOut:
    """Ask for a channel we do not index yet (ADR-002 §3, the "user" source).

    A channel somebody already indexed is not a request at all: it is attached to the
    asker's library immediately, because channels are indexed once for the platform.
    """
    result = await discovery.suggest(session, claims.user_id, body.ref)
    channel = await session.get(Channel, result.channel_id) if result.channel_id else None
    return SuggestChannelOut(
        status=result.status,
        channel=_out(channel) if channel else None,
        candidate_id=result.candidate_id,
    )


@router.delete("/library/channels/{channel_id}", status_code=204)
async def remove_channel(channel_id: int, claims: WritableClaims, session: SessionDep) -> None:
    await channels.unsubscribe(session, claims.user_id, channel_id)


@router.get("/channels/categories", response_model=list[CategoryOut])
async def categories(_: Claims, session: SessionDep) -> list[CategoryOut]:
    return [CategoryOut.model_validate(c) for c in await channels.list_categories(session)]


@router.get("/channels/featured", response_model=Page[ChannelOut])
async def featured(
    _: Claims,
    session: SessionDep,
    category_id: int | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[ChannelOut]:
    after = decode_cursor(cursor, 1)
    rows = await channels.list_featured(
        session, category_id, cursor_int(after[0]) if after else None, limit + 1
    )
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return Page(items=[_out(c) for c in rows[:limit]], next_cursor=next_cursor)


@router.get("/channels/{channel_id}", response_model=ChannelOut)
async def channel_detail(channel_id: int, _: Claims, session: SessionDep) -> ChannelOut:
    return _out(await channels.get_channel(session, channel_id))
