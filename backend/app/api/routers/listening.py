"""The listening features: artist essentials, follows and "Fans also like", lyrics
and credits, hiding or snoozing a song, private sessions, long-track progress,
folders and pins, Blend, Daylist, the DJ and Connect."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from app.api.deps import Claims, HttpDep, RedisDep, SessionDep, SettingsDep, WritableClaims
from app.errors import InvalidInput, NotFound
from app.schemas import (
    ArtistOut,
    BlendInviteOut,
    BlendOut,
    ConnectCommandIn,
    ConnectCommandOut,
    CreditsOut,
    DaylistOut,
    DeviceOut,
    DjOut,
    DjSegmentOut,
    FilePlaylistIn,
    FolderIn,
    FolderOut,
    FollowArtistOut,
    HeartbeatIn,
    HeartbeatOut,
    HiddenOut,
    HideIn,
    LyricsOut,
    Page,
    PinOut,
    PrivateSessionIn,
    PrivateSessionOut,
    ProgressIn,
    ProgressOut,
    ThisIsOut,
    TrackIdsIn,
    TrackOut,
    TrackSourceOut,
)
from app.services import (
    blends,
    connect,
    folders,
    follows,
    lastfm,
    library,
    lyrics,
    pins,
    playlists,
    recommendations,
    related,
    taste,
)

router = APIRouter(prefix="/v1", tags=["listening"])

Limit = Annotated[int, Query(ge=1, le=50)]


# ── artists: This Is, follow ──


@router.get("/artists/{artist_id}/this-is", response_model=ThisIsOut)
async def this_is(
    artist_id: int,
    claims: Claims,
    session: SessionDep,
    http: HttpDep,
    settings: SettingsDep,
) -> ThisIsOut:
    found = await lastfm.this_is(session, artist_id, http, settings)
    artist = await library.get_artist(session, found.artist_id, claims.lang)
    items = await library.hydrate_tracks(
        session, found.track_ids, claims.lang, viewer_id=claims.user_id
    )
    return ThisIsOut(
        artist=artist,
        source="lastfm" if found.source == "lastfm" else "plays",
        items=items,
    )


@router.put("/artists/{artist_id}/follow", response_model=FollowArtistOut)
async def follow_artist(
    artist_id: int, claims: WritableClaims, session: SessionDep
) -> FollowArtistOut:
    await follows.follow(session, claims.user_id, artist_id)
    return FollowArtistOut(
        following=True, followers=await follows.followers_count(session, artist_id)
    )


@router.delete("/artists/{artist_id}/follow", response_model=FollowArtistOut)
async def unfollow_artist(
    artist_id: int, claims: WritableClaims, session: SessionDep
) -> FollowArtistOut:
    await follows.unfollow(session, claims.user_id, artist_id)
    return FollowArtistOut(
        following=False, followers=await follows.followers_count(session, artist_id)
    )


@router.get("/artists/{artist_id}/related", response_model=list[ArtistOut])
async def related_artists(artist_id: int, claims: Claims, session: SessionDep) -> list[ArtistOut]:
    """Fans also like: from our similarity matrix, co-listening, then collaborators."""
    out: list[ArtistOut] = []
    for found in await related.related_artist_ids(session, artist_id):
        try:
            out.append(await library.get_artist(session, found, claims.lang))
        except NotFound:
            continue
    return out


@router.get("/tracks/{track_id}/credits", response_model=CreditsOut)
async def track_credits(track_id: int, claims: Claims, session: SessionDep) -> CreditsOut:
    found = await related.credits(session, track_id)
    track = await library.get_track(session, found.track_id, claims.lang, claims.user_id)
    return CreditsOut(
        track=track,
        genre=found.genre,
        file_name=found.file_name,
        mime_type=found.mime_type,
        file_size=found.file_size,
        first_posted_at=found.first_posted_at,
        channels=found.channels,
        sources=[
            TrackSourceOut(
                channel_id=s.channel_id,
                username=s.username,
                title=s.title,
                subscribers_count=s.subscribers_count,
                posted_at=s.posted_at,
            )
            for s in found.sources
        ],
    )


@router.get("/me/artists", response_model=list[ArtistOut])
async def followed_artists(claims: Claims, session: SessionDep) -> list[ArtistOut]:
    """The artists this user follows, newest follow first."""
    out: list[ArtistOut] = []
    for artist_id in await follows.followed_ids(session, claims.user_id):
        try:
            artist = await library.get_artist(session, artist_id, claims.lang)
        except NotFound:
            continue
        out.append(artist)
    return out


# ── lyrics ──


@router.get("/tracks/{track_id}/lyrics", response_model=LyricsOut)
async def track_lyrics(
    track_id: int,
    claims: Claims,
    session: SessionDep,
    http: HttpDep,
    settings: SettingsDep,
) -> LyricsOut:
    found = await lyrics.get(session, http, settings, track_id)
    if found is None:
        return LyricsOut(track_id=track_id, found=False)
    return LyricsOut(
        track_id=found.track_id, synced=found.synced, plain=found.plain, source=found.source
    )


# ── hide a song ──


@router.put("/tracks/{track_id}/hide", response_model=HiddenOut)
async def hide_track(
    track_id: int, claims: WritableClaims, session: SessionDep, body: HideIn | None = None
) -> HiddenOut:
    """Hides a song for good, or snoozes it (``snooze_days``) until it may come back."""
    until = await taste.hide(session, claims.user_id, track_id, body.snooze_days if body else None)
    return HiddenOut(track_id=track_id, until=until)


@router.delete("/tracks/{track_id}/hide", status_code=204)
async def unhide_track(track_id: int, claims: WritableClaims, session: SessionDep) -> Response:
    await taste.unhide(session, claims.user_id, track_id)
    return Response(status_code=204)


@router.get("/me/hidden", response_model=list[int])
async def hidden_tracks(claims: Claims, session: SessionDep) -> list[int]:
    return sorted(await taste.hidden_ids(session, claims.user_id))


@router.get("/me/hidden/tracks", response_model=list[HiddenOut])
async def hidden_detail(claims: Claims, session: SessionDep) -> list[HiddenOut]:
    """Hidden and snoozed songs, newest first, with when each comes back."""
    return [
        HiddenOut(track_id=track_id, until=until)
        for track_id, until in await taste.hidden(session, claims.user_id)
    ]


# ── private session ──


@router.get("/me/private-session", response_model=PrivateSessionOut)
async def private_session(claims: Claims, session: SessionDep) -> PrivateSessionOut:
    return PrivateSessionOut(private_until=await taste.private_until(session, claims.user_id))


@router.put("/me/private-session", response_model=PrivateSessionOut)
async def set_private_session(
    body: PrivateSessionIn, claims: WritableClaims, session: SessionDep
) -> PrivateSessionOut:
    until = await taste.set_private(session, claims.user_id, body.on)
    return PrivateSessionOut(private_until=until)


# ── long tracks: podcasts, sets, audiobooks ──


@router.put("/tracks/{track_id}/progress", response_model=ProgressOut)
async def save_progress(
    track_id: int, body: ProgressIn, claims: WritableClaims, session: SessionDep
) -> ProgressOut:
    finished = await taste.save_progress(session, claims.user_id, track_id, body.position_s)
    return ProgressOut(track_id=track_id, position_s=body.position_s, finished=finished)


@router.get("/me/progress", response_model=list[ProgressOut])
async def progress(
    claims: Claims,
    session: SessionDep,
    ids: Annotated[list[int], Query(max_length=200)],
) -> list[ProgressOut]:
    found = await taste.progress_for(session, claims.user_id, ids)
    return [
        ProgressOut(track_id=track_id, position_s=position, finished=finished)
        for track_id, (position, finished) in found.items()
    ]


@router.get("/me/in-progress", response_model=Page[TrackOut])
async def in_progress(claims: Claims, session: SessionDep, limit: Limit = 20) -> Page[TrackOut]:
    ids = await taste.in_progress(session, claims.user_id, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


# ── recommendations for a set of tracks: Enhance and Smart Shuffle ──


@router.post("/recommendations/for-tracks", response_model=Page[TrackOut])
async def for_tracks(
    body: TrackIdsIn, claims: Claims, session: SessionDep, limit: Limit = 20
) -> Page[TrackOut]:
    ids = await recommendations.for_tracks(session, claims.user_id, body.track_ids, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


@router.get("/playlists/{playlist_id}/recommendations", response_model=Page[TrackOut])
async def playlist_recommendations(
    playlist_id: int, claims: Claims, session: SessionDep, limit: Limit = 10
) -> Page[TrackOut]:
    await playlists.access(session, playlist_id, claims.user_id)
    seeds = await playlists.track_ids(session, playlist_id)
    ids = await recommendations.for_tracks(session, claims.user_id, seeds, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


# ── folders ──


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(claims: Claims, session: SessionDep) -> list[FolderOut]:
    return [
        FolderOut(id=folder.id, name=folder.name, playlists=count)
        for folder, count in await folders.list_for(session, claims.user_id)
    ]


@router.post("/folders", response_model=FolderOut, status_code=201)
async def create_folder(body: FolderIn, claims: WritableClaims, session: SessionDep) -> FolderOut:
    folder = await folders.create(session, claims.user_id, body.name)
    return FolderOut(id=folder.id, name=folder.name)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: int, body: FolderIn, claims: WritableClaims, session: SessionDep
) -> FolderOut:
    folder = await folders.rename(session, folder_id, claims.user_id, body.name)
    return FolderOut(id=folder.id, name=folder.name)


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: int, claims: WritableClaims, session: SessionDep) -> Response:
    await folders.remove(session, folder_id, claims.user_id)
    return Response(status_code=204)


@router.put("/playlists/{playlist_id}/folder", status_code=204)
async def file_playlist(
    playlist_id: int, body: FilePlaylistIn, claims: WritableClaims, session: SessionDep
) -> Response:
    await folders.file_playlist(session, playlist_id, claims.user_id, body.folder_id)
    return Response(status_code=204)


# ── Blend ──


@router.post("/blends/invite", response_model=BlendInviteOut)
async def blend_invite(
    claims: WritableClaims, redis: RedisDep, settings: SettingsDep
) -> BlendInviteOut:
    code = await blends.invite(redis, claims.user_id)
    return BlendInviteOut(
        code=code, share_url=f"https://t.me/{settings.bot_username}?startapp=bl_{code}"
    )


def _blend_out(view: blends.BlendView) -> BlendOut:
    return BlendOut(
        id=view.id,
        other_user_id=view.other_user_id,
        other_name=view.other_name,
        playlist_id=view.playlist_id,
        match_pct=view.match_pct,
        refreshed_at=view.refreshed_at,
    )


@router.post("/blends/join/{code}", response_model=BlendOut)
async def blend_join(
    code: str, claims: WritableClaims, session: SessionDep, redis: RedisDep
) -> BlendOut:
    blend = await blends.join(session, redis, code, claims.user_id)
    views = await blends.list_for(session, claims.user_id)
    return _blend_out(next(v for v in views if v.id == blend.id))


@router.get("/blends", response_model=list[BlendOut])
async def list_blends(claims: Claims, session: SessionDep) -> list[BlendOut]:
    return [_blend_out(view) for view in await blends.list_for(session, claims.user_id)]


@router.post("/blends/{blend_id}/refresh", response_model=BlendOut)
async def refresh_blend(blend_id: int, claims: WritableClaims, session: SessionDep) -> BlendOut:
    await blends.refresh(session, blend_id, claims.user_id)
    views = await blends.list_for(session, claims.user_id)
    return _blend_out(next(v for v in views if v.id == blend_id))


@router.delete("/blends/{blend_id}", status_code=204)
async def leave_blend(blend_id: int, claims: WritableClaims, session: SessionDep) -> Response:
    await blends.leave(session, blend_id, claims.user_id)
    return Response(status_code=204)


# ── Daylist and DJ ──


@router.get("/daylist", response_model=DaylistOut)
async def get_daylist(claims: WritableClaims, session: SessionDep) -> DaylistOut:
    """The playlist for right now; rebuilt when the part of the day changes."""
    playlist, part = await recommendations.daylist(session, claims.user_id)
    ids = await playlists.track_ids(session, playlist.id)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return DaylistOut(playlist_id=playlist.id, name=playlist.name, part=part, items=items)


@router.get("/dj", response_model=DjOut)
async def dj(claims: Claims, session: SessionDep) -> DjOut:
    segments = await recommendations.dj_session(session, claims.user_id)
    wanted = sorted({t for segment in segments for t in segment.track_ids})
    hydrated = {
        t.id: t
        for t in await library.hydrate_tracks(
            session, wanted, claims.lang, viewer_id=claims.user_id
        )
    }
    return DjOut(
        segments=[
            DjSegmentOut(
                kind=segment.kind,
                items=[hydrated[t] for t in segment.track_ids if t in hydrated],
            )
            for segment in segments
            if any(t in hydrated for t in segment.track_ids)
        ]
    )


# ── pins ──


@router.get("/me/pins", response_model=list[PinOut])
async def list_pins(claims: Claims, session: SessionDep) -> list[PinOut]:
    return [
        PinOut(kind=kind, ref_id=ref_id)
        for kind, ref_id in await pins.list_for(session, claims.user_id)
    ]


@router.put("/me/pins/{kind}/{ref_id}", response_model=list[PinOut])
async def pin(
    kind: Literal["playlist", "artist"], ref_id: int, claims: WritableClaims, session: SessionDep
) -> list[PinOut]:
    await pins.pin(session, claims.user_id, kind, ref_id)
    return await list_pins(claims, session)


@router.delete("/me/pins/{kind}/{ref_id}", response_model=list[PinOut])
async def unpin(
    kind: Literal["playlist", "artist"], ref_id: int, claims: WritableClaims, session: SessionDep
) -> list[PinOut]:
    await pins.unpin(session, claims.user_id, kind, ref_id)
    return await list_pins(claims, session)


# ── Connect: your devices ──


@router.post("/connect/heartbeat", response_model=HeartbeatOut)
async def connect_heartbeat(
    body: HeartbeatIn, claims: Claims, session: SessionDep, redis: RedisDep
) -> HeartbeatOut:
    others, commands = await connect.heartbeat(
        redis,
        claims.user_id,
        connect.Device(
            id=body.device_id,
            name=body.name,
            kind=body.kind,
            seen=0,
            track_id=body.state.track_id,
            position_s=body.state.position_s,
            playing=body.state.playing,
        ),
    )
    wanted = {d.track_id for d in others if d.track_id} | {t for c in commands for t in c.track_ids}
    hydrated = {
        t.id: t
        for t in await library.hydrate_tracks(
            session, sorted(wanted), claims.lang, viewer_id=claims.user_id
        )
    }
    return HeartbeatOut(
        devices=[
            DeviceOut(
                id=d.id,
                name=d.name,
                kind=d.kind,
                playing=d.playing,
                position_s=d.position_s,
                track=hydrated.get(d.track_id) if d.track_id else None,
            )
            for d in others
        ],
        commands=[
            ConnectCommandOut(
                action=c.action,
                sender=c.sender,
                index=c.index,
                position_s=c.position_s,
                playing=c.playing,
                items=[hydrated[t] for t in c.track_ids if t in hydrated],
            )
            for c in commands
        ],
    )


@router.post("/connect/command", status_code=204)
async def connect_command(body: ConnectCommandIn, claims: Claims, redis: RedisDep) -> Response:
    if body.action == "transfer" and not body.track_ids:
        raise InvalidInput("nothing to play", reason="empty")
    await connect.send(
        redis,
        claims.user_id,
        body.target,
        connect.Command(
            action=body.action,
            sender=body.sender,
            track_ids=tuple(body.track_ids),
            index=min(body.index, max(len(body.track_ids) - 1, 0)),
            position_s=body.position_s,
            playing=body.playing,
        ),
    )
    return Response(status_code=204)


@router.delete("/connect/devices/{device_id}", status_code=204)
async def connect_forget(device_id: str, claims: Claims, redis: RedisDep) -> Response:
    await connect.forget(redis, claims.user_id, device_id[:64])
    return Response(status_code=204)
