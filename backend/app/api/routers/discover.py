"""Discover, trending, similar tracks and radio.

Every response is hydrated through ``library.hydrate_tracks``, so a section costs one
extra query no matter how many tracks it holds (the N+1 guard test covers this).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import Claims, SessionDep, WritableClaims
from app.schemas import DiscoverOut, Page, SectionOut, TrackOut
from app.services import library, recommendations

router = APIRouter(prefix="/v1", tags=["discover"])

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/discover", response_model=DiscoverOut)
async def discover(claims: Claims, session: SessionDep) -> DiscoverOut:
    sections = await recommendations.discover_sections(session, claims.user_id)
    wanted = {track_id for section in sections for track_id in section.track_ids}
    hydrated = await library.hydrate_tracks(
        session, sorted(wanted), claims.lang, viewer_id=claims.user_id
    )
    by_id = {track.id: track for track in hydrated}
    return DiscoverOut(
        sections=[
            SectionOut(
                id=section.id,
                kind=section.kind,
                title=section.title_fa if claims.lang == "fa" else section.title_en,
                playlist_id=section.playlist_id,
                items=[by_id[track_id] for track_id in section.track_ids if track_id in by_id],
            )
            for section in sections
        ]
    )


@router.get("/trending", response_model=Page[TrackOut])
async def trending(
    claims: Claims,
    session: SessionDep,
    window: Literal["24h", "7d", "30d"] = "7d",
    kind: Literal["plays", "most_added", "rising"] = "plays",
    limit: Limit = 30,
) -> Page[TrackOut]:
    ids = await recommendations.trending_ids(session, window, kind, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


@router.get("/tracks/{track_id}/similar", response_model=Page[TrackOut])
async def similar(
    track_id: int, claims: Claims, session: SessionDep, limit: Limit = 20
) -> Page[TrackOut]:
    ids = await recommendations.similar_ids(session, track_id, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


@router.get("/tracks/{track_id}/radio", response_model=Page[TrackOut])
async def radio(
    track_id: int, claims: Claims, session: SessionDep, limit: Limit = 50
) -> Page[TrackOut]:
    ids = await recommendations.radio_ids(session, track_id, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


@router.post("/discover/refresh", response_model=DiscoverOut)
async def refresh(claims: WritableClaims, session: SessionDep) -> DiscoverOut:
    """Regenerates this user's mixes now instead of waiting for the nightly job."""
    await recommendations.refresh_for_user(session, claims.user_id)
    return await discover(claims, session)
