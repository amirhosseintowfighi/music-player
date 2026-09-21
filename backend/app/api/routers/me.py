from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import Claims, SessionDep, SettingsDep, State, WritableClaims
from app.schemas import (
    LikeOut,
    Page,
    PlaybackStateIn,
    PlaybackStateOut,
    PlayEventIn,
    TrackOut,
)
from app.services import history, library, sending

router = APIRouter(prefix="/v1", tags=["me"])

Limit = Annotated[int, Query(ge=1, le=100)]


@router.put("/tracks/{track_id}/like", response_model=LikeOut)
async def like_track(track_id: int, claims: WritableClaims, session: SessionDep) -> LikeOut:
    await history.like(session, claims.user_id, track_id)
    return LikeOut(liked=True, likes_count=await history.likes_count(session, track_id))


@router.delete("/tracks/{track_id}/like", response_model=LikeOut)
async def unlike_track(track_id: int, claims: WritableClaims, session: SessionDep) -> LikeOut:
    await history.unlike(session, claims.user_id, track_id)
    return LikeOut(liked=False, likes_count=await history.likes_count(session, track_id))


@router.get("/library/likes", response_model=Page[TrackOut])
async def liked_tracks(
    claims: Claims, session: SessionDep, cursor: str | None = None, limit: Limit = 50
) -> Page[TrackOut]:
    ids, next_cursor = await history.liked_page(session, claims.user_id, cursor, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=next_cursor)


@router.get("/library/recent", response_model=Page[TrackOut])
async def recently_played(claims: Claims, session: SessionDep, limit: Limit = 30) -> Page[TrackOut]:
    ids = await history.recent_ids(session, claims.user_id, limit)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return Page(items=items, next_cursor=None)


@router.post("/history", status_code=204)
async def record_play(body: PlayEventIn, claims: WritableClaims, session: SessionDep) -> Response:
    """Called by the player when a track finishes or is skipped."""
    await history.record_play(
        session,
        claims.user_id,
        history.PlayEvent(
            track_id=body.track_id,
            duration_played=body.duration_played,
            completed=body.completed,
            source=body.source,
            source_id=body.source_id,
            device=None,
        ),
    )
    return Response(status_code=204)


@router.put("/me/playback", status_code=204)
async def save_playback(
    body: PlaybackStateIn, claims: WritableClaims, session: SessionDep
) -> Response:
    await history.save_playback(
        session,
        claims.user_id,
        track_id=body.track_id,
        position_s=body.position_s,
        queue=body.queue,
        queue_index=body.queue_index,
        shuffle=body.shuffle,
        repeat_mode=body.repeat_mode,
        speed=body.speed,
    )
    return Response(status_code=204)


@router.get("/me/playback", response_model=PlaybackStateOut)
async def load_playback(claims: Claims, session: SessionDep) -> PlaybackStateOut:
    """Resume point, so another device can continue where this one stopped."""
    state = await history.load_playback(session, claims.user_id)
    if state is None:
        return PlaybackStateOut()
    items = await library.hydrate_tracks(
        session, list(state.queue), claims.lang, viewer_id=claims.user_id
    )
    return PlaybackStateOut(
        track_id=state.track_id,
        position_s=state.position_s,
        queue=list(state.queue),
        queue_index=state.queue_index,
        shuffle=state.shuffle,
        repeat_mode=state.repeat_mode,
        speed=state.speed,
        items=items,
        updated_at=state.updated_at,
    )


@router.post("/tracks/{track_id}/send", status_code=204)
async def send_to_chat(
    track_id: int, claims: WritableClaims, session: SessionDep, settings: SettingsDep, state: State
) -> Response:
    """Sends the audio file to the user's own chat with the bot (the edge uploads it)."""
    track = await library.get_track(session, track_id, claims.lang, claims.user_id)
    await sending.send_to_chat(
        session,
        state.http,
        settings,
        claims,
        track_id,
        track.title,
        ", ".join(a.name for a in track.artists),
    )
    return Response(status_code=204)
