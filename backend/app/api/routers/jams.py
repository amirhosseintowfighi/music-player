"""Jam: a shared queue and playhead for listening together (see services/jams)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Claims, RedisDep, SessionDep, SettingsDep, WritableClaims
from app.config import Settings
from app.errors import NotFound
from app.schemas import (
    CreateJamIn,
    JamAddIn,
    JamControlIn,
    JamItemOut,
    JamMemberOut,
    JamOut,
    JamSettingsIn,
)
from app.security.tokens import AccessClaims
from app.services import jams, library

router = APIRouter(prefix="/v1/jams", tags=["jams"])


def share_url(settings: Settings, code: str) -> str:
    return f"https://t.me/{settings.bot_username}?startapp=jam_{code}"


async def _out(
    jam: jams.Jam,
    claims: AccessClaims,
    session: AsyncSession,
    settings: Settings,
    known_qrev: int | None = None,
) -> JamOut:
    items: list[JamItemOut] | None = None
    if known_qrev != jam.qrev:
        ids = [item.track_id for item in jam.items]
        tracks = {
            t.id: t for t in await library.hydrate_tracks(session, ids, claims.lang, claims.user_id)
        }
        # Repeats are allowed in a jam queue, so map back rather than zip.
        items = [
            JamItemOut(track=tracks[item.track_id], added_by=item.added_by)
            for item in jam.items
            if item.track_id in tracks
        ]
    members = sorted(jam.members.values(), key=lambda m: (m.user_id != jam.host_id, m.joined_ms))
    return JamOut(
        code=jam.code,
        share_url=share_url(settings, jam.code),
        host_id=jam.host_id,
        is_host=claims.user_id == jam.host_id,
        can_control=jam.can_control(claims.user_id),
        guests_can_control=jam.guests_can_control,
        members=[
            JamMemberOut(
                user_id=m.user_id,
                first_name=m.first_name,
                username=m.username,
                is_host=m.user_id == jam.host_id,
            )
            for m in members
        ],
        index=jam.index,
        playing=jam.playing,
        position_s=round(jam.position(jams.now_ms()), 3),
        rev=jam.rev,
        qrev=jam.qrev,
        queue_length=len(jam.items),
        items=items,
    )


@router.post("", response_model=JamOut, status_code=201)
async def create_jam(
    body: CreateJamIn,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> JamOut:
    jam = await jams.create(
        session,
        redis,
        claims.user_id,
        body.track_ids,
        index=body.index,
        position_s=body.position_s,
        playing=body.playing,
    )
    return await _out(jam, claims, session, settings)


@router.get("/current", response_model=JamOut | None)
async def current_jam(
    claims: Claims, session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> JamOut | None:
    """The jam this user is in, if any — so reopening the app puts them back in it."""
    code = await jams.code_of(redis, claims.user_id)
    if code is None:
        return None
    try:
        jam = await jams.load(redis, code)
    except NotFound:
        return None
    if claims.user_id not in jam.members:
        return None
    return await _out(jam, claims, session, settings)


@router.get("/{code}", response_model=JamOut)
async def get_jam(
    code: str,
    claims: Claims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    qrev: Annotated[int | None, Query(ge=0)] = None,
) -> JamOut:
    jam = await jams.load(redis, code)
    return await _out(jam, claims, session, settings, known_qrev=qrev)


@router.post("/{code}/join", response_model=JamOut)
async def join_jam(
    code: str, claims: WritableClaims, session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> JamOut:
    jam = await jams.join(session, redis, code, claims.user_id)
    return await _out(jam, claims, session, settings)


@router.post("/{code}/leave", status_code=204)
async def leave_jam(code: str, claims: WritableClaims, redis: RedisDep) -> Response:
    await jams.leave(redis, code, claims.user_id)
    return Response(status_code=204)


@router.delete("/{code}", status_code=204)
async def end_jam(code: str, claims: WritableClaims, redis: RedisDep) -> Response:
    await jams.end(redis, code, claims.user_id)
    return Response(status_code=204)


@router.post("/{code}/control", response_model=JamOut)
async def control_jam(
    code: str,
    body: JamControlIn,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    qrev: Annotated[int | None, Query(ge=0)] = None,
) -> JamOut:
    jam = await jams.control(
        redis,
        code,
        claims.user_id,
        body.action,
        position_s=body.position_s,
        index=body.index,
        expected_index=body.expected_index,
    )
    return await _out(jam, claims, session, settings, known_qrev=qrev)


@router.post("/{code}/queue", response_model=JamOut)
async def add_to_jam(
    code: str,
    body: JamAddIn,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> JamOut:
    jam = await jams.add(session, redis, code, claims.user_id, body.track_ids, body.position)
    return await _out(jam, claims, session, settings)


@router.delete("/{code}/queue/{index}", response_model=JamOut)
async def remove_from_jam(
    code: str,
    index: int,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> JamOut:
    jam = await jams.remove(redis, code, claims.user_id, index)
    return await _out(jam, claims, session, settings)


@router.patch("/{code}", response_model=JamOut)
async def configure_jam(
    code: str,
    body: JamSettingsIn,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    qrev: Annotated[int | None, Query(ge=0)] = None,
) -> JamOut:
    jam = await jams.configure(redis, code, claims.user_id, body.guests_can_control)
    return await _out(jam, claims, session, settings, known_qrev=qrev)
