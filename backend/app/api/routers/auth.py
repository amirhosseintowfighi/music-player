from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import update

from app.api.deps import (
    Claims,
    HttpDep,
    RedisDep,
    SessionDep,
    SettingsDep,
    WritableClaims,
    rate_limit_ip,
)
from app.errors import NotFound
from app.models import User
from app.schemas import (
    GateChannelOut,
    GateOut,
    LangIn,
    MeOut,
    RefreshIn,
    TelegramLoginIn,
    TokenOut,
)
from app.services import auth, gate, plans, users

router = APIRouter(prefix="/v1", tags=["auth"])

UserAgent = Annotated[str | None, Header(alias="User-Agent")]


async def _me(session: SessionDep, user: User) -> MeOut:
    plan = await plans.get_plan(session, users.effective_plan(user))
    return MeOut(
        id=user.id,
        tg_id=user.tg_id,
        username=user.username,
        first_name=user.first_name,
        lang="en" if user.lang == "en" else "fa",
        plan=plan.code,
        premium_until=user.premium_until,
        features=sorted(plan.features),
        limits=plan.limits,
        referral_code=user.referral_code,
        public_profile=user.public_profile,
    )


@router.post("/auth/telegram", response_model=TokenOut, dependencies=[Depends(rate_limit_ip)])
async def login_telegram(
    body: TelegramLoginIn, session: SessionDep, settings: SettingsDep, ua: UserAgent = None
) -> TokenOut:
    pair, data = await auth.login_with_init_data(session, settings, body.init_data, ua)
    return TokenOut(
        access_token=pair.access_token,
        access_expires_at=pair.access_expires_at,
        refresh_token=pair.refresh_token,
        refresh_expires_at=pair.refresh_expires_at,
        start_param=data.start_param,
        me=await _me(session, pair.user),
    )


@router.post("/auth/refresh", response_model=TokenOut, dependencies=[Depends(rate_limit_ip)])
async def refresh_token(
    body: RefreshIn, session: SessionDep, settings: SettingsDep, ua: UserAgent = None
) -> TokenOut:
    pair = await auth.refresh(session, settings, body.refresh_token, ua)
    return TokenOut(
        access_token=pair.access_token,
        access_expires_at=pair.access_expires_at,
        refresh_token=pair.refresh_token,
        refresh_expires_at=pair.refresh_expires_at,
        me=await _me(session, pair.user),
    )


@router.post("/auth/logout", status_code=204, dependencies=[Depends(rate_limit_ip)])
async def logout(body: RefreshIn, session: SessionDep) -> None:
    await auth.logout(session, body.refresh_token)


@router.get("/me", response_model=MeOut)
async def get_me(claims: Claims, session: SessionDep) -> MeOut:
    user = await users.get_user(session, claims.user_id)
    if user is None:
        raise NotFound("user not found")
    return await _me(session, user)


@router.patch("/me/lang", response_model=MeOut)
async def set_lang(body: LangIn, claims: WritableClaims, session: SessionDep) -> MeOut:
    await session.execute(update(User).where(User.id == claims.user_id).values(lang=body.lang))
    user = await users.get_user(session, claims.user_id)
    if user is None:
        raise NotFound("user not found")
    await session.refresh(user)
    return await _me(session, user)


@router.get("/gate", response_model=GateOut)
async def join_gate(
    claims: Claims,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    http: HttpDep,
) -> GateOut:
    """Asked once when the app opens: is this listener in the required channels?

    Cached for a few minutes per user, and open by default — no required channels
    configured, or Telegram not answering, both mean "let them in".
    """
    absent = await gate.missing(session, redis, http, settings, claims.tg_id)
    return GateOut(missing=[GateChannelOut(username=c.username, url=c.url) for c in absent])


@router.post("/gate/recheck", response_model=GateOut)
async def join_gate_recheck(
    claims: Claims,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    http: HttpDep,
) -> GateOut:
    """ "I joined" — ask Telegram again now instead of waiting for the cache."""
    await gate.forget(redis, claims.tg_id)
    absent = await gate.missing(session, redis, http, settings, claims.tg_id)
    return GateOut(missing=[GateChannelOut(username=c.username, url=c.url) for c in absent])
