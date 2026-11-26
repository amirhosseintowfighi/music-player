"""Wake-up alarms (0020): set them here, the bot rings them, ``/wake`` plays them."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Claims, SessionDep, WritableClaims
from app.models import Alarm
from app.schemas import AlarmIn, AlarmOut, AlarmPatchIn, WakeOut
from app.services import alarms, library

router = APIRouter(prefix="/v1/alarms", tags=["alarms"])


def _out(alarm: Alarm) -> AlarmOut:
    return AlarmOut.model_validate(alarm, from_attributes=True)


@router.get("", response_model=list[AlarmOut])
async def my_alarms(claims: Claims, session: SessionDep) -> list[AlarmOut]:
    return [_out(a) for a in await alarms.list_for(session, claims.user_id)]


@router.post("", response_model=AlarmOut, status_code=201)
async def create_alarm(body: AlarmIn, claims: WritableClaims, session: SessionDep) -> AlarmOut:
    alarm = await alarms.create(
        session,
        claims.user_id,
        minute_of_day=body.minute_of_day,
        days=body.days,
        tz_offset_minutes=body.tz_offset_minutes,
        label=body.label,
        source=body.source,
        playlist_id=body.playlist_id,
    )
    return _out(alarm)


@router.patch("/{alarm_id}", response_model=AlarmOut)
async def update_alarm(
    alarm_id: int, body: AlarmPatchIn, claims: WritableClaims, session: SessionDep
) -> AlarmOut:
    alarm = await alarms.get_owned(session, alarm_id, claims.user_id)
    await alarms.update(
        session,
        alarm,
        minute_of_day=body.minute_of_day,
        days=body.days,
        tz_offset_minutes=body.tz_offset_minutes,
        label=body.label,
        source=body.source,
        playlist_id=body.playlist_id,
        enabled=body.enabled,
    )
    return _out(alarm)


@router.delete("/{alarm_id}", status_code=204)
async def delete_alarm(alarm_id: int, claims: WritableClaims, session: SessionDep) -> None:
    alarm = await alarms.get_owned(session, alarm_id, claims.user_id)
    await session.delete(alarm)


@router.get("/{alarm_id}/wake", response_model=WakeOut)
async def wake(alarm_id: int, claims: Claims, session: SessionDep) -> WakeOut:
    """What the alarm plays: opened from the button on the bot's wake-up message."""
    alarm = await alarms.get_owned(session, alarm_id, claims.user_id)
    ids = await alarms.wake_queue(session, alarm)
    items = await library.hydrate_tracks(session, ids, claims.lang, viewer_id=claims.user_id)
    return WakeOut(alarm=_out(alarm), items=items)
