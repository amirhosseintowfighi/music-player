from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import Claims, SessionDep, SettingsDep, WritableClaims
from app.config import Settings
from app.models import Playlist
from app.schemas import (
    AddTracksIn,
    CreatePlaylistIn,
    CreateSmartPlaylistIn,
    MoveTrackIn,
    PlaylistDetailOut,
    PlaylistOut,
    SmartPreviewOut,
    SmartRules,
    SmartRulesIn,
    UpdatePlaylistIn,
)
from app.services import library, playlists, smart_playlists

router = APIRouter(prefix="/v1/playlists", tags=["playlists"])


def _out(
    playlist: Playlist, settings: Settings, *, is_owner: bool = True, can_edit: bool = True
) -> PlaylistOut:
    share_url = (
        f"https://t.me/{settings.bot_username}?startapp=pl_{playlist.share_slug}"
        if playlist.share_slug and playlist.is_public
        else None
    )
    return PlaylistOut(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        kind=playlist.kind,
        is_public=playlist.is_public,
        is_collaborative=playlist.is_collaborative,
        share_slug=playlist.share_slug,
        share_url=share_url,
        tracks_count=playlist.tracks_count,
        duration_total=playlist.duration_total,
        is_owner=is_owner,
        can_edit=can_edit,
        updated_at=playlist.updated_at,
        created_at=playlist.created_at,
        # A folder is its owner's way of filing; a collaborator sees it unfiled.
        folder_id=playlist.folder_id if is_owner else None,
        exclude_from_taste=playlist.exclude_from_taste if is_owner else False,
        rules=SmartRules.model_validate(playlist.rules)
        if smart_playlists.is_smart(playlist) and playlist.rules is not None
        else None,
        refreshed_at=playlist.refreshed_at if smart_playlists.is_smart(playlist) else None,
    )


@router.get("", response_model=list[PlaylistOut])
async def my_playlists(
    claims: Claims, session: SessionDep, settings: SettingsDep
) -> list[PlaylistOut]:
    rows = await playlists.list_for_user(session, claims.user_id)
    return [_out(p, settings, is_owner=p.user_id == claims.user_id) for p in rows]


@router.post("", response_model=PlaylistDetailOut, status_code=201)
async def create_playlist(
    body: CreatePlaylistIn, claims: WritableClaims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    playlist = await playlists.create(session, claims.user_id, body.name, body.description)
    if body.track_ids:
        await playlists.add_tracks(session, playlist.id, claims.user_id, body.track_ids)
        await session.refresh(playlist)
    return PlaylistDetailOut(**_out(playlist, settings).model_dump(), items=[])


async def _detail(
    playlist: Playlist, claims: Claims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    ids = await playlists.track_ids(session, playlist.id)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return PlaylistDetailOut(**_out(playlist, settings).model_dump(), items=items)


@router.post("/smart", response_model=PlaylistDetailOut, status_code=201)
async def create_smart_playlist(
    body: CreateSmartPlaylistIn, claims: WritableClaims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    """A playlist made of rules; its songs are picked now and kept up to date."""
    playlist = await smart_playlists.create(
        session, claims.user_id, body.name, body.description, body.rules
    )
    return await _detail(playlist, claims, session, settings)


@router.post("/smart/preview", response_model=SmartPreviewOut)
async def preview_smart_playlist(
    body: SmartRulesIn, claims: Claims, session: SessionDep
) -> SmartPreviewOut:
    """What a set of rules would pick, without saving anything: for the rule editor."""
    ids, total = await smart_playlists.matching(session, claims.user_id, body.rules)
    items = await library.hydrate_tracks(session, ids[:20], claims.lang, viewer_id=claims.user_id)
    return SmartPreviewOut(total=total, items=items)


@router.put("/{playlist_id}/rules", response_model=PlaylistDetailOut)
async def update_smart_rules(
    playlist_id: int,
    body: SmartRulesIn,
    claims: WritableClaims,
    session: SessionDep,
    settings: SettingsDep,
) -> PlaylistDetailOut:
    playlist = await smart_playlists.set_rules(session, playlist_id, claims.user_id, body.rules)
    return await _detail(playlist, claims, session, settings)


@router.post("/{playlist_id}/refresh", response_model=PlaylistDetailOut)
async def refresh_smart_playlist(
    playlist_id: int, claims: WritableClaims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    playlist = await smart_playlists.owned(session, playlist_id, claims.user_id)
    await smart_playlists.rebuild(session, playlist)
    return await _detail(playlist, claims, session, settings)


@router.get("/{playlist_id}", response_model=PlaylistDetailOut)
async def playlist_detail(
    playlist_id: int, claims: Claims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    found = await playlists.access(session, playlist_id, claims.user_id)
    if found.playlist.user_id == claims.user_id and claims.act_as_admin is None:
        # A smart playlist keeps itself current: opening a stale one rebuilds it.
        await smart_playlists.refresh_if_stale(session, found.playlist)
    ids = await playlists.track_ids(session, playlist_id)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    base = _out(
        found.playlist,
        settings,
        is_owner=found.playlist.user_id == claims.user_id,
        can_edit=found.can_edit,
    )
    return PlaylistDetailOut(**base.model_dump(), items=items)


@router.patch("/{playlist_id}", response_model=PlaylistOut)
async def update_playlist(
    playlist_id: int,
    body: UpdatePlaylistIn,
    claims: WritableClaims,
    session: SessionDep,
    settings: SettingsDep,
) -> PlaylistOut:
    playlist = await playlists.update_details(
        session,
        playlist_id,
        claims.user_id,
        name=body.name,
        description=body.description,
        is_public=body.is_public,
        is_collaborative=body.is_collaborative,
        exclude_from_taste=body.exclude_from_taste,
    )
    await session.refresh(playlist)
    return _out(playlist, settings)


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int, claims: WritableClaims, session: SessionDep) -> None:
    await playlists.remove(session, playlist_id, claims.user_id)


@router.post("/{playlist_id}/tracks", response_model=PlaylistOut)
async def add_tracks(
    playlist_id: int,
    body: AddTracksIn,
    claims: WritableClaims,
    session: SessionDep,
    settings: SettingsDep,
) -> PlaylistOut:
    await playlists.add_tracks(session, playlist_id, claims.user_id, body.track_ids)
    found = await playlists.access(session, playlist_id, claims.user_id)
    await session.refresh(found.playlist)
    return _out(found.playlist, settings, is_owner=found.playlist.user_id == claims.user_id)


@router.delete("/{playlist_id}/tracks/{track_id}", status_code=204)
async def remove_track(
    playlist_id: int, track_id: int, claims: WritableClaims, session: SessionDep
) -> None:
    await playlists.remove_track(session, playlist_id, claims.user_id, track_id)


@router.post("/{playlist_id}/move", status_code=204)
async def move_track(
    playlist_id: int, body: MoveTrackIn, claims: WritableClaims, session: SessionDep
) -> Response:
    await playlists.move_track(
        session, playlist_id, claims.user_id, body.track_id, body.after_track_id
    )
    return Response(status_code=204)


@router.get("/shared/{slug}", response_model=PlaylistDetailOut)
async def shared_playlist(
    slug: str, claims: Claims, session: SessionDep, settings: SettingsDep
) -> PlaylistDetailOut:
    """Opened from a share link (t.me/<bot>?startapp=pl_<slug>)."""
    playlist = await playlists.by_slug(session, slug)
    ids = await playlists.track_ids(session, playlist.id)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    base = _out(
        playlist,
        settings,
        is_owner=playlist.user_id == claims.user_id,
        can_edit=playlist.user_id == claims.user_id,
    )
    return PlaylistDetailOut(**base.model_dump(), items=items)


@router.post("/shared/{slug}/join", response_model=PlaylistOut)
async def join_collaboration(
    slug: str, claims: WritableClaims, session: SessionDep, settings: SettingsDep
) -> PlaylistOut:
    playlist = await playlists.by_slug(session, slug)
    await playlists.add_collaborator(session, playlist.id, playlist.user_id, claims.user_id)
    return _out(playlist, settings, is_owner=False, can_edit=True)
