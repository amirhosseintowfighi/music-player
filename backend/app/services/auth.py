from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import Forbidden, Unauthorized
from app.models import RefreshToken, User
from app.security.initdata import InitData, InitDataError, validate_init_data
from app.security.tokens import AccessClaims, encode_access, hash_refresh_token, new_refresh_token
from app.services import plans, users
from tmusic_common.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    access_expires_at: int
    refresh_token: str
    refresh_expires_at: int
    user: User


async def _issue(
    session: AsyncSession,
    settings: Settings,
    user: User,
    family_id: uuid.UUID,
    device: str | None,
) -> TokenPair:
    plan_code = users.effective_plan(user)
    plan = await plans.get_plan(session, plan_code)
    lang: Literal["fa", "en"] = "en" if user.lang == "en" else "fa"
    access, access_exp = encode_access(
        AccessClaims(
            user_id=user.id,
            tg_id=user.tg_id,
            plan=plan.code,
            lang=lang,
            features=tuple(sorted(plan.features)),
        ),
        settings.jwt_private_key.get_secret_value(),
        settings.jwt_issuer,
        settings.access_ttl_s,
    )
    refresh, refresh_hash = new_refresh_token()
    expires = datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_s)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=refresh_hash,
            device=(device or "")[:200] or None,
            expires_at=expires,
        )
    )
    await session.flush()
    return TokenPair(access, access_exp, refresh, int(expires.timestamp()), user)


async def login_with_init_data(
    session: AsyncSession, settings: Settings, raw_init_data: str, device: str | None
) -> tuple[TokenPair, InitData]:
    try:
        data = validate_init_data(
            raw_init_data, settings.bot_token.get_secret_value(), settings.auth_max_age_s
        )
    except InitDataError as exc:
        log.info("auth.init_data_rejected", reason=str(exc))
        raise Unauthorized("invalid init data") from exc
    user = await users.upsert_telegram_user(session, data.user)
    if user.is_banned:
        raise Forbidden("user is banned", reason=user.ban_reason)
    return await _issue(session, settings, user, uuid.uuid4(), device), data


async def refresh(
    session: AsyncSession, settings: Settings, token: str, device: str | None
) -> TokenPair:
    """Rotate a refresh token. Reusing a consumed token revokes the whole family."""
    row = (
        await session.scalars(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(token))
            .with_for_update()
        )
    ).one_or_none()
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise Unauthorized("invalid refresh token")
    if row.used_at is not None:
        await revoke_family(session, row.family_id)
        await session.commit()  # the revocation must survive the error response
        log.warning("auth.refresh_reuse_detected", user_id=row.user_id)
        raise Unauthorized("refresh token reuse")
    row.used_at = now
    user = await session.get(User, row.user_id)
    if user is None:
        raise Unauthorized("invalid refresh token")
    if user.is_banned:
        await revoke_family(session, row.family_id)
        raise Forbidden("user is banned", reason=user.ban_reason)
    return await _issue(session, settings, user, row.family_id, device)


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def logout(session: AsyncSession, token: str) -> None:
    family = await session.scalar(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if family is not None:
        await revoke_family(session, family)


async def impersonate(
    session: AsyncSession, settings: Settings, user: User, admin_id: int, ttl_s: int = 900
) -> tuple[str, int]:
    """A short-lived, read-only access token for an admin looking at a user's app.

    No refresh token is issued: impersonation must expire on its own, and the
    ``act_as_admin`` claim makes every write endpoint refuse the token.
    """
    plan = await plans.get_plan(session, users.effective_plan(user))
    lang: Literal["fa", "en"] = "en" if user.lang == "en" else "fa"
    return encode_access(
        AccessClaims(
            user_id=user.id,
            tg_id=user.tg_id,
            plan=plan.code,
            lang=lang,
            features=tuple(sorted(plan.features)),
            act_as_admin=admin_id,
        ),
        settings.jwt_private_key.get_secret_value(),
        settings.jwt_issuer,
        ttl_s,
    )
