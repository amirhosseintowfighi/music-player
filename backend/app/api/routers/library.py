from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import Claims, SessionDep, WritableClaims
from app.errors import LimitReached
from app.schemas import (
    AlbumOut,
    AlbumPageOut,
    AlbumRef,
    ArtistOut,
    ArtistPageOut,
    Page,
    PaletteIn,
    ReportTrackIn,
    ReportTrackOut,
    TrackOut,
)
from app.services import channels, library, reports
from app.services.library import TrackFilters

router = APIRouter(prefix="/v1", tags=["library"])

Limit = Annotated[int, Query(ge=1, le=100)]
LanguageFilter = Literal["fa", "en", "ar", "tr", "ku", "other"]


def track_filters(
    channel_id: int | None = None,
    artist_id: int | None = None,
    album: Annotated[str | None, Query(max_length=200)] = None,
    language: LanguageFilter | None = None,
    year: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    min_duration: Annotated[int | None, Query(ge=0)] = None,
    max_duration: Annotated[int | None, Query(ge=0)] = None,
) -> TrackFilters:
    return TrackFilters(channel_id, artist_id, album, language, year, min_duration, max_duration)


Filters = Annotated[TrackFilters, Depends(track_filters)]


@router.get("/library/tracks", response_model=Page[TrackOut])
async def my_tracks(
    claims: Claims,
    session: SessionDep,
    filters: Filters,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[TrackOut]:
    items, nxt = await library.library_tracks(
        session, claims.user_id, filters, cursor, limit, claims.lang
    )
    return Page(items=items, next_cursor=nxt)


@router.get("/library/artists", response_model=Page[ArtistOut])
async def my_artists(
    claims: Claims, session: SessionDep, cursor: str | None = None, limit: Limit = 50
) -> Page[ArtistOut]:
    items, nxt = await library.library_artists(session, claims.user_id, cursor, limit, claims.lang)
    return Page(items=items, next_cursor=nxt)


@router.get("/library/albums", response_model=list[AlbumOut])
async def my_albums(claims: Claims, session: SessionDep, limit: Limit = 50) -> list[AlbumOut]:
    return await library.library_albums(session, claims.user_id, limit, claims.lang)


@router.get("/tracks/{track_id}", response_model=TrackOut)
async def track_detail(track_id: int, claims: Claims, session: SessionDep) -> TrackOut:
    return await library.get_track(session, track_id, claims.lang, claims.user_id)


@router.get("/channels/{channel_id}/tracks", response_model=Page[TrackOut])
async def channel_tracks(
    channel_id: int,
    claims: Claims,
    session: SessionDep,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[TrackOut]:
    await channels.get_channel(session, channel_id)
    items, nxt = await library.channel_tracks(
        session, channel_id, cursor, limit, claims.lang, claims.user_id
    )
    return Page(items=items, next_cursor=nxt)


@router.get("/artists/{artist_id}", response_model=ArtistOut)
async def artist_detail(artist_id: int, claims: Claims, session: SessionDep) -> ArtistOut:
    return await library.get_artist(session, artist_id, claims.lang)


@router.get("/albums/{artist_id}", response_model=AlbumPageOut)
async def album_page(
    artist_id: int,
    claims: Claims,
    session: SessionDep,
    name: Annotated[str, Query(min_length=1, max_length=300)],
) -> AlbumPageOut:
    """An album's own page. The name travels as a query parameter because album
    names contain slashes, dots and everything else a path segment dislikes."""
    album, year, items = await library.album_page(
        session, artist_id, name, claims.lang, viewer_id=claims.user_id
    )
    return AlbumPageOut(album=album, year=year, items=items)


@router.get("/artists/{artist_id}/page", response_model=ArtistPageOut)
async def artist_page(artist_id: int, claims: Claims, session: SessionDep) -> ArtistPageOut:
    """The artist page's header: who they are, their best-liked songs, their albums."""
    artist = await library.get_artist(session, artist_id, claims.lang)
    top, albums = await library.artist_overview(
        session, artist_id, claims.lang, viewer_id=claims.user_id
    )
    return ArtistPageOut(
        artist=artist,
        top_tracks=top,
        albums=[AlbumRef(**album) for album in albums],
    )


@router.get("/artists/{artist_id}/tracks", response_model=Page[TrackOut])
async def artist_tracks(
    artist_id: int,
    claims: Claims,
    session: SessionDep,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[TrackOut]:
    artist = await library.get_artist(session, artist_id, claims.lang)
    items, nxt = await library.artist_tracks(
        session, artist.id, cursor, limit, claims.lang, claims.user_id
    )
    return Page(items=items, next_cursor=nxt)


@router.post("/tracks/{track_id}/report", response_model=ReportTrackOut, status_code=201)
async def report_track(
    track_id: int, body: ReportTrackIn, claims: WritableClaims, session: SessionDep
) -> ReportTrackOut:
    """Wrong metadata, or content that should not be here.

    Filing the same report twice is not an error: it updates the open one, because a
    second report from the same person means "still wrong", not "a second problem".
    """
    if await reports.open_reports_by(session, claims.user_id) >= reports.MAX_OPEN_PER_USER:
        raise LimitReached("too many open reports", kind="reports", limit=reports.MAX_OPEN_PER_USER)
    report = await reports.report_track(
        session, claims.user_id, track_id, reason=body.reason, details=body.details
    )
    return ReportTrackOut(id=report.id, status=report.status)


@router.put("/tracks/{track_id}/palette", status_code=204)
async def set_palette(
    track_id: int, body: PaletteIn, claims: WritableClaims, session: SessionDep
) -> None:
    """The colours this client found in the artwork (ADR-003 phase 12).

    Written once and never overwritten: the first client to render a track pays the
    canvas cost, everybody after it gets the gradient with the metadata. A wrong
    value is cosmetic, which is why an ordinary user is allowed to write it at all —
    but the shape is validated, so it can only ever be three colours.
    """
    assert claims.user_id
    await library.remember_palette(session, track_id, body.colors)
