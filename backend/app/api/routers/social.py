"""Public profiles, following, the friends feed, Wrapped and notification settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import Claims, SessionDep, WritableClaims
from app.schemas import (
    ConnectionOut,
    FollowOut,
    FriendActivityOut,
    NotificationPrefsIn,
    NotificationPrefsOut,
    ProfileOut,
    PublicProfileIn,
    WrappedOut,
)
from app.services import library, notifications, social

router = APIRouter(prefix="/v1", tags=["social"])

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/users/{user_id}/profile", response_model=ProfileOut)
async def profile(user_id: int, claims: Claims, session: SessionDep) -> ProfileOut:
    found = await social.profile(session, user_id, claims.user_id)
    tracks = await library.hydrate_tracks(
        session, found.top_tracks, claims.lang, viewer_id=claims.user_id
    )
    return ProfileOut(
        user_id=found.user_id,
        first_name=found.first_name,
        username=found.username,
        is_pro=found.is_pro,
        followers=found.followers,
        following=found.following,
        playlists=found.playlists,
        tracks_played=found.tracks_played,
        joined_at=found.joined_at,
        is_me=found.is_me,
        is_following=found.is_following,
        top_artists=found.top_artists,
        top_tracks=tracks,
        public_playlists=found.public_playlists,
    )


@router.get("/me/profile", response_model=ProfileOut)
async def my_profile(claims: Claims, session: SessionDep) -> ProfileOut:
    return await profile(claims.user_id, claims, session)


@router.put("/users/{user_id}/follow", response_model=FollowOut)
async def follow(user_id: int, claims: WritableClaims, session: SessionDep) -> FollowOut:
    created = await social.follow(session, claims.user_id, user_id)
    return FollowOut(following=True, changed=created)


@router.delete("/users/{user_id}/follow", response_model=FollowOut)
async def unfollow(user_id: int, claims: WritableClaims, session: SessionDep) -> FollowOut:
    removed = await social.unfollow(session, claims.user_id, user_id)
    return FollowOut(following=False, changed=removed)


@router.get("/users/{user_id}/followers", response_model=list[ConnectionOut])
async def followers(
    user_id: int, claims: Claims, session: SessionDep, limit: Limit = 50
) -> list[ConnectionOut]:
    rows = await social.connections(
        session, user_id, claims.user_id, direction="followers", limit=limit
    )
    return [ConnectionOut(**row) for row in rows]


@router.get("/users/{user_id}/following", response_model=list[ConnectionOut])
async def following(
    user_id: int, claims: Claims, session: SessionDep, limit: Limit = 50
) -> list[ConnectionOut]:
    rows = await social.connections(
        session, user_id, claims.user_id, direction="following", limit=limit
    )
    return [ConnectionOut(**row) for row in rows]


@router.get("/social/feed", response_model=list[FriendActivityOut])
async def friends_feed(
    claims: Claims, session: SessionDep, limit: Limit = 30
) -> list[FriendActivityOut]:
    """What the people you follow have been listening to. Empty until you follow someone."""
    rows = await social.friends_activity(session, claims.user_id, limit)
    tracks = await library.hydrate_tracks(
        session, [row["track_id"] for row in rows], claims.lang, viewer_id=claims.user_id
    )
    by_id = {track.id: track for track in tracks}
    return [
        FriendActivityOut(
            user_id=row["user_id"],
            first_name=row["first_name"],
            username=row["username"],
            played_at=row["played_at"],
            track=by_id[row["track_id"]],
        )
        for row in rows
        if row["track_id"] in by_id
    ]


@router.get("/me/wrapped", response_model=WrappedOut)
async def wrapped(
    claims: Claims,
    session: SessionDep,
    year: Annotated[int, Query(ge=2020, le=2100)] | None = None,
) -> WrappedOut:
    payload = await social.wrapped(session, claims.user_id, year)
    tracks = await library.hydrate_tracks(
        session,
        [entry["id"] for entry in payload["top_tracks"]],
        claims.lang,
        viewer_id=claims.user_id,
    )
    return WrappedOut(**payload, tracks=tracks)


@router.put("/me/public-profile", status_code=204)
async def set_public_profile(
    body: PublicProfileIn, claims: WritableClaims, session: SessionDep
) -> Response:
    await social.set_public_profile(session, claims.user_id, body.public)
    return Response(status_code=204)


@router.get("/me/notifications", response_model=NotificationPrefsOut)
async def get_notification_prefs(claims: Claims, session: SessionDep) -> NotificationPrefsOut:
    return NotificationPrefsOut(prefs=await notifications.get_prefs(session, claims.user_id))


@router.put("/me/notifications", response_model=NotificationPrefsOut)
async def set_notification_prefs(
    body: NotificationPrefsIn, claims: WritableClaims, session: SessionDep
) -> NotificationPrefsOut:
    prefs = await notifications.set_prefs(
        session, claims.user_id, body.prefs, body.tz_offset_minutes
    )
    return NotificationPrefsOut(prefs=prefs)
