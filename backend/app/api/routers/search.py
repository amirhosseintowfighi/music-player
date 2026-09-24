from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app import metrics
from app.api.deps import Claims, MeiliDep, SessionDep
from app.api.routers.library import Filters
from app.schemas import ArtistOut, SearchOut, SuggestOut
from app.services import channels, library, search
from app.services.library import TrackFilters

router = APIRouter(prefix="/v1/search", tags=["search"])

Q = Annotated[str, Query(min_length=1, max_length=100)]


@router.get("", response_model=SearchOut)
async def search_tracks(
    claims: Claims,
    session: SessionDep,
    meili: MeiliDep,
    q: Q,
    filters: Filters,
    scope: Literal["library", "global"] = "library",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=950)] = 0,
) -> SearchOut:
    # Relevance-ranked results are paged by offset (bounded by maxTotalHits); lists elsewhere
    # use keyset cursors.
    scope_ids = (
        await channels.user_channel_ids(session, claims.user_id) if scope == "library" else None
    )
    hit = await search.search_tracks(
        session, meili, q, channel_ids=scope_ids, filters=filters, limit=limit, offset=offset
    )
    if offset == 0:
        await search.record_history(session, claims.user_id, q, hit.total)
    metrics.SEARCHES.labels(scope, str(hit.degraded).lower()).inc()
    items = await library.hydrate_tracks(session, hit.ids, claims.lang, claims.user_id)
    return SearchOut(
        items=items, total=hit.total, offset=offset, limit=limit, degraded=hit.degraded
    )


@router.get("/artists", response_model=list[ArtistOut])
async def search_artists(claims: Claims, session: SessionDep, q: Q) -> list[ArtistOut]:
    """Artists matching what was typed, so a name in the search box is a way in."""
    return await library.search_artists(session, q, claims.lang)


@router.get("/suggest", response_model=SuggestOut)
async def suggest(
    claims: Claims,
    session: SessionDep,
    meili: MeiliDep,
    q: Annotated[str, Query(max_length=100)] = "",
) -> SuggestOut:
    history = await search.recent_history(session, claims.user_id, q, 5)
    tracks = []
    if q.strip():
        scope_ids = await channels.user_channel_ids(session, claims.user_id)
        hit = await search.search_tracks(
            session, meili, q, channel_ids=scope_ids or None, filters=TrackFilters(),
            limit=6, offset=0,
        )  # fmt: skip
        tracks = await library.hydrate_tracks(session, hit.ids, claims.lang, claims.user_id)
    return SuggestOut(history=history, tracks=tracks)


@router.delete("/history", status_code=204)
async def clear_history(claims: Claims, session: SessionDep) -> None:
    await search.clear_history(session, claims.user_id)
