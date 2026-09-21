"""User-facing channel management. Channels are global; users subscribe (ADR-0003)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidInput, LimitReached, NotFound
from app.models import Blacklist, Channel, ChannelCategory, User, UserChannel
from app.services import plans, users

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_TG_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
# t.me paths that are not channel usernames
_RESERVED = {"joinchat", "addstickers", "share", "proxy", "socks", "c", "s", "iv", "login"}


@dataclass(frozen=True, slots=True)
class ChannelRef:
    username: str | None = None
    tg_channel_id: int | None = None
    title: str | None = None


def parse_channel_ref(raw: str) -> ChannelRef:
    """Accept ``@name``, ``name``, ``t.me/name``, ``https://t.me/name/123``, ``tg://resolve``.

    Only parsed, never fetched: no outbound request is made from user input (no SSRF).
    Private invite links cannot be indexed by MTProto and are rejected with a code the
    client uses to explain the bot-admin path.
    """
    text = raw.strip()
    if text.startswith("@"):
        candidate = text[1:]
    elif text.startswith("tg://"):
        query = urlsplit(text).query
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        candidate = params.get("domain", "")
    elif "/" in text or "." in text:
        url = text if "://" in text else f"https://{text}"
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if host not in _TG_HOSTS:
            raise InvalidInput("not a telegram link", reason="not_telegram")
        segments = [s for s in parts.path.split("/") if s]
        if not segments:
            raise InvalidInput("missing channel name", reason="invalid")
        first = segments[0]
        if first.startswith("+") or first.lower() in {"joinchat", "c"}:
            raise InvalidInput("private channel link", reason="private_link")
        if first.lower() == "s" and len(segments) > 1:  # t.me/s/<name> web preview
            first = segments[1]
        elif first.lower() in _RESERVED:
            raise InvalidInput("not a channel link", reason="invalid")
        candidate = first
    else:
        candidate = text
    if not _USERNAME.match(candidate):
        raise InvalidInput("invalid channel username", reason="invalid")
    return ChannelRef(username=candidate)


async def _is_blacklisted(session: AsyncSession, ref: ChannelRef) -> bool:
    conditions = []
    if ref.username:
        conditions.append(
            (Blacklist.entity_type == "channel_username")
            & (func.lower(Blacklist.value) == ref.username.lower())
        )
    if ref.tg_channel_id is not None:
        conditions.append(
            (Blacklist.entity_type == "channel") & (Blacklist.value == str(ref.tg_channel_id))
        )
    if not conditions:
        return False
    return (await session.scalar(select(Blacklist.id).where(or_(*conditions)).limit(1))) is not None


async def get_or_create_channel(
    session: AsyncSession,
    ref: ChannelRef,
    *,
    added_by: int | None,
    source: str = "mtproto",
) -> tuple[Channel, bool]:
    if await _is_blacklisted(session, ref):
        raise Forbidden("channel is blocked", reason="blacklisted")
    lookup = (
        Channel.tg_channel_id == ref.tg_channel_id
        if ref.tg_channel_id is not None
        else Channel.username == ref.username
    )
    existing = (await session.scalars(select(Channel).where(lookup))).one_or_none()
    if existing is None and ref.tg_channel_id is not None and ref.username:
        existing = (
            await session.scalars(select(Channel).where(Channel.username == ref.username))
        ).one_or_none()
    if existing is not None:
        if existing.status == "blacklisted":
            raise Forbidden("channel is blocked", reason="blacklisted")
        if existing.tg_channel_id is None and ref.tg_channel_id is not None:
            existing.tg_channel_id = ref.tg_channel_id
        if existing.status == "failed" and existing.source == "mtproto":
            # Let the indexer retry a channel that failed earlier (e.g. it was private then).
            existing.status, existing.status_reason = "pending", None
            existing.next_index_at = datetime.now(UTC)
        return existing, False

    stmt = (
        insert(Channel)
        .values(
            username=ref.username,
            tg_channel_id=ref.tg_channel_id,
            title=ref.title,
            source=source,
            # Where the tracks will come from (ADR-002). A channel the bot administers
            # posts to us directly and must never be queued for the web crawler — its
            # preview may not even exist.
            source_type="bot_member" if source == "bot_admin" else "web_preview",
            is_public=ref.username is not None,
            status="pending" if source == "mtproto" else "active",
            added_by_user_id=added_by,
        )
        .on_conflict_do_nothing()
        .returning(Channel)
    )
    created = (await session.scalars(stmt)).one_or_none()
    if created is not None:
        return created, True
    # Lost a race with a concurrent insert of the same channel.
    return (await session.scalars(select(Channel).where(lookup))).one(), False


async def subscribe(
    session: AsyncSession, user_id: int, ref: ChannelRef, *, source: str = "mtproto"
) -> tuple[Channel, bool]:
    user = (
        await session.scalars(select(User).where(User.id == user_id).with_for_update())
    ).one_or_none()
    if user is None:
        raise NotFound("user not found")
    channel, created = await get_or_create_channel(session, ref, added_by=user_id, source=source)

    already = await session.scalar(
        select(UserChannel.channel_id).where(
            UserChannel.user_id == user_id, UserChannel.channel_id == channel.id
        )
    )
    if already is not None:
        return channel, created

    plan = await plans.get_plan(session, users.effective_plan(user))
    count = await session.scalar(
        select(func.count()).select_from(UserChannel).where(UserChannel.user_id == user_id)
    )
    if not plan.allows("channels", count or 0):
        raise LimitReached("channel limit reached", kind="channels", limit=plan.limit("channels"))

    await session.execute(
        insert(UserChannel).values(user_id=user_id, channel_id=channel.id).on_conflict_do_nothing()
    )
    await session.execute(
        update(Channel)
        .where(Channel.id == channel.id)
        .values(subscribers_count=Channel.subscribers_count + 1)
    )
    await session.refresh(channel)
    return channel, created


async def unsubscribe(session: AsyncSession, user_id: int, channel_id: int) -> None:
    result = await session.execute(
        delete(UserChannel)
        .where(UserChannel.user_id == user_id, UserChannel.channel_id == channel_id)
        .returning(UserChannel.channel_id)
    )
    if result.first() is None:
        raise NotFound("channel not in library")
    await session.execute(
        update(Channel)
        .where(Channel.id == channel_id)
        .values(subscribers_count=func.greatest(Channel.subscribers_count - 1, 0))
    )


async def list_user_channels(
    session: AsyncSession, user_id: int
) -> list[tuple[Channel, UserChannel]]:
    rows = await session.execute(
        select(Channel, UserChannel)
        .join(UserChannel, UserChannel.channel_id == Channel.id)
        .where(UserChannel.user_id == user_id)
        .order_by(UserChannel.added_at.desc(), Channel.id.desc())
    )
    return [(c, uc) for c, uc in rows.tuples()]


async def user_channel_ids(session: AsyncSession, user_id: int) -> list[int]:
    return list(
        (
            await session.scalars(
                select(UserChannel.channel_id).where(UserChannel.user_id == user_id)
            )
        ).all()
    )


async def get_channel(session: AsyncSession, channel_id: int) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None or channel.status == "blacklisted":
        raise NotFound("channel not found")
    return channel


async def list_featured(
    session: AsyncSession, category_id: int | None, after_id: int | None, limit: int
) -> list[Channel]:
    stmt = select(Channel).where(Channel.is_featured, Channel.status == "active")
    if category_id is not None:
        stmt = stmt.where(Channel.category_id == category_id)
    if after_id is not None:
        stmt = stmt.where(Channel.id < after_id)
    return list((await session.scalars(stmt.order_by(Channel.id.desc()).limit(limit))).all())


async def list_categories(session: AsyncSession) -> list[ChannelCategory]:
    return list(
        (await session.scalars(select(ChannelCategory).order_by(ChannelCategory.position))).all()
    )
