"""Channel discovery: how the catalogue grows (ADR-002 §3).

Three ways in, one queue out:

- **seed** — an admin imports a list of usernames (CLI or the panel's CSV box).
  An admin saying "index these" *is* the approval, so seeds skip the queue.
- **user** — somebody asks for a channel we do not have. Their id is remembered, so
  a channel ten people asked for outranks one nobody did.
- **crawl_mention** — a username seen in a link while crawling. Never indexed on its
  own: the whole point of the queue is that a mention is a hint, not a decision.

A rejected candidate is blacklisted, which is what stops the crawler from suggesting
the same channel every week for the rest of time.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidInput, NotFound
from app.models import Blacklist, Channel, ChannelCandidate
from app.services import channels as channels_service
from tmusic_common.logging import get_logger

log = get_logger(__name__)

# Deliberately the same shape the rest of the code accepts for a channel reference.
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MAX_IMPORT = 500


def clean_username(raw: str) -> str | None:
    """A username, or None when the line is not one. Never raises: imports are messy."""
    try:
        ref = channels_service.parse_channel_ref(raw)
    except InvalidInput:
        return None
    return ref.username.lower() if ref.username and _USERNAME.match(ref.username) else None


def score_of(
    *,
    tracks_estimate: int | None,
    audio_ratio: float | None,
    posts_per_day: float | None,
    requesters: int,
    mentions: int,
) -> float:
    """How interesting a candidate is, 0–100, so the admin reads the queue top-down.

    Size is logarithmic on purpose: the difference between 200 and 2,000 tracks
    matters, the difference between 20,000 and 40,000 does not. A channel people
    actually asked for beats a big one nobody mentioned.
    """
    size = 10.0 * math.log10(1 + max(tracks_estimate or 0, 0))
    music = 30.0 * min(max(audio_ratio or 0.0, 0.0), 1.0)
    activity = min(max(posts_per_day or 0.0, 0.0), 20.0)
    demand = 5.0 * requesters + 2.0 * max(mentions - 1, 0)
    return round(min(size + music + activity + demand, 100.0), 2)


async def _blacklisted(session: AsyncSession, username: str) -> bool:
    found = await session.scalar(
        select(Blacklist.id)
        .where(
            Blacklist.entity_type == "channel_username",
            func.lower(Blacklist.value) == username.lower(),
        )
        .limit(1)
    )
    return found is not None


async def submit(
    session: AsyncSession,
    username: str,
    *,
    source: str,
    user_id: int | None = None,
    discovered_from: int | None = None,
) -> ChannelCandidate | None:
    """Records a candidate. Returns None when it is already a channel or blacklisted.

    Idempotent by username: a second sighting bumps the mention count, a second
    requester is appended, and the score is recomputed from both.
    """
    name = clean_username(username)
    if name is None or await _blacklisted(session, name):
        return None
    known = await session.scalar(select(Channel.id).where(Channel.username == name))
    if known is not None:
        return None

    row_id = await session.scalar(
        insert(ChannelCandidate)
        .values(
            username=name,
            source=source,
            discovered_from_channel_id=discovered_from,
            requested_by_user_ids=[user_id] if user_id else [],
        )
        .on_conflict_do_update(
            index_elements=[ChannelCandidate.username],
            set_={
                "mention_count": ChannelCandidate.mention_count + 1,
                "updated_at": func.now(),
            },
        )
        .returning(ChannelCandidate.id)
    )
    # populate_existing, or an object already in this session keeps the counts it had
    # before the upsert and the score is computed from stale numbers.
    row = (
        await session.scalars(
            select(ChannelCandidate)
            .where(ChannelCandidate.id == row_id)
            .execution_options(populate_existing=True)
        )
    ).one()

    if user_id and user_id not in row.requested_by_user_ids:
        # ARRAY columns are replaced, not mutated in place, or SQLAlchemy misses it.
        row.requested_by_user_ids = [*row.requested_by_user_ids, user_id]
    row.score = score_of(
        tracks_estimate=row.tracks_estimate,
        audio_ratio=row.audio_ratio,
        posts_per_day=row.posts_per_day,
        requesters=len(row.requested_by_user_ids),
        mentions=row.mention_count,
    )
    await session.flush()
    return row


async def record_mentions(
    session: AsyncSession, usernames: list[str], *, discovered_from: int | None
) -> int:
    """What the crawler calls for every page of links it walked past."""
    added = 0
    for name in sorted({u.lower().lstrip("@") for u in usernames}):
        if await submit(session, name, source="crawl_mention", discovered_from=discovered_from):
            added += 1
    return added


# ── the review queue ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QueuePage:
    items: list[ChannelCandidate]
    total: int


async def queue(
    session: AsyncSession, *, status: str = "pending", limit: int = 50, offset: int = 0
) -> QueuePage:
    """The panel's queue: best first, so a distracted admin still sees what matters."""
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(ChannelCandidate)
            .where(ChannelCandidate.status == status)
        )
        or 0
    )
    rows = await session.scalars(
        select(ChannelCandidate)
        .where(ChannelCandidate.status == status)
        .order_by(ChannelCandidate.score.desc(), ChannelCandidate.created_at)
        .limit(limit)
        .offset(offset)
    )
    return QueuePage(items=list(rows.all()), total=total)


async def approve(
    session: AsyncSession, candidate_id: int, *, admin_id: int | None = None
) -> Channel:
    """Turns a candidate into a channel the crawler will pick up on its next tick."""
    candidate = await session.get(ChannelCandidate, candidate_id)
    if candidate is None:
        raise NotFound("candidate not found")
    if await _blacklisted(session, candidate.username):
        raise Forbidden("channel is blocked", reason="blacklisted")

    channel = await ensure_channel(session, candidate.username, title=candidate.title)
    candidate.status = "approved"
    candidate.approved_channel_id = channel.id
    candidate.reviewed_by = admin_id
    candidate.reviewed_at = datetime.now(UTC)

    # Whoever asked for it gets it in their library the moment it is approved — the
    # channel is indexed once for the platform, so there is no per-user job to wait on.
    for user_id in candidate.requested_by_user_ids:
        await _attach(session, user_id, channel.id)
    await session.flush()
    log.info("discovery.approved", username=candidate.username, channel_id=channel.id)
    return channel


async def ensure_channel(
    session: AsyncSession, username: str, *, title: str | None = None
) -> Channel:
    """The channel row a crawl needs: due now, web_preview, nothing else assumed."""
    existing = (
        await session.scalars(select(Channel).where(Channel.username == username))
    ).one_or_none()
    if existing is not None:
        if existing.status == "failed":
            existing.status, existing.status_reason = "pending", None
            existing.crawl_status, existing.fail_count = "idle", 0
            existing.next_crawl_at = datetime.now(UTC)
        return existing
    return (
        await session.scalars(
            insert(Channel)
            .values(
                username=username,
                title=title,
                source="mtproto",
                source_type="web_preview",
                is_public=True,
                status="pending",
                crawl_status="idle",
                next_crawl_at=datetime.now(UTC),
            )
            .returning(Channel)
        )
    ).one()


async def _attach(session: AsyncSession, user_id: int, channel_id: int) -> None:
    await session.execute(
        text(
            "INSERT INTO user_channels (user_id, channel_id) VALUES (:u, :c) ON CONFLICT DO NOTHING"
        ).bindparams(u=user_id, c=channel_id)
    )
    await session.execute(
        text(
            "UPDATE channels SET subscribers_count = subscribers_count + 1"
            " WHERE id = :c AND EXISTS (SELECT 1 FROM user_channels"
            "   WHERE user_id = :u AND channel_id = :c)"
        ).bindparams(u=user_id, c=channel_id)
    )


async def reject(
    session: AsyncSession,
    candidate_ids: list[int],
    *,
    reason: str = "",
    admin_id: int | None = None,
) -> int:
    """Rejects candidates and blacklists them, so discovery cannot suggest them again."""
    if not candidate_ids:
        return 0
    rows = list(
        await session.scalars(
            select(ChannelCandidate).where(ChannelCandidate.id.in_(candidate_ids))
        )
    )
    now = datetime.now(UTC)
    for candidate in rows:
        candidate.status = "rejected"
        candidate.reject_reason = reason[:500] or "rejected"
        candidate.reviewed_by = admin_id
        candidate.reviewed_at = now
        await session.execute(
            insert(Blacklist)
            .values(
                entity_type="channel_username",
                value=candidate.username,
                reason=reason[:500] or "candidate rejected",
                created_by=admin_id,
            )
            .on_conflict_do_nothing()
        )
    await session.flush()
    log.info("discovery.rejected", count=len(rows), reason=reason)
    return len(rows)


# ── seeding ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ImportResult:
    created: int
    existing: int
    blocked: int
    invalid: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "existing": self.existing,
            "blocked": self.blocked,
            "invalid": self.invalid[:20],
        }


async def import_usernames(
    session: AsyncSession, raw: str, *, admin_id: int | None = None
) -> ImportResult:
    """Batch seed: one username, link or CSV row per line.

    An admin pasting a list has already made the decision, so these become channels
    directly instead of queue entries — but a blacklisted name is still refused, since
    that decision was also an admin's.
    """
    created = existing = blocked = 0
    invalid: list[str] = []
    seen: set[str] = set()
    for line in raw.replace(",", "\n").splitlines():
        entry = line.strip()
        if not entry or entry.lower() in {"username", "channel", "channels"}:
            continue
        if len(seen) >= MAX_IMPORT:
            break
        name = clean_username(entry)
        if name is None:
            invalid.append(entry[:64])
            continue
        if name in seen:
            continue
        seen.add(name)
        if await _blacklisted(session, name):
            blocked += 1
            continue
        known = await session.scalar(select(Channel.id).where(Channel.username == name))
        if known is not None:
            existing += 1
            continue
        channel = await ensure_channel(session, name)
        await session.execute(
            insert(ChannelCandidate)
            .values(
                username=name,
                source="seed",
                status="approved",
                approved_channel_id=channel.id,
                reviewed_by=admin_id,
                reviewed_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[ChannelCandidate.username],
                set_={"status": "approved", "approved_channel_id": channel.id},
            )
        )
        created += 1
    await session.flush()
    log.info("discovery.imported", created=created, existing=existing, blocked=blocked)
    return ImportResult(created=created, existing=existing, blocked=blocked, invalid=invalid)


# ── user requests ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Suggestion:
    status: str  # "subscribed" | "queued"
    channel_id: int | None = None
    candidate_id: int | None = None


async def suggest(session: AsyncSession, user_id: int, raw: str) -> Suggestion:
    """A user asking for a channel the catalogue does not have.

    If we already index it, they simply get it — that is the whole benefit of indexing
    once per platform. Otherwise their request is queued and counted.
    """
    ref = channels_service.parse_channel_ref(raw)
    name = (ref.username or "").lower()
    if not name:
        raise InvalidInput("invalid channel username", reason="invalid")
    if await _blacklisted(session, name):
        raise Forbidden("channel is blocked", reason="blacklisted")

    known = (await session.scalars(select(Channel).where(Channel.username == name))).one_or_none()
    if known is not None and known.status != "blacklisted":
        channel, _ = await channels_service.subscribe(session, user_id, ref)
        return Suggestion(status="subscribed", channel_id=channel.id)

    candidate = await submit(session, name, source="user", user_id=user_id)
    if candidate is None:
        raise Forbidden("channel is blocked", reason="blacklisted")
    return Suggestion(status="queued", candidate_id=candidate.id)


# ── probing (what makes the score real) ───────────────────────────────────────

PROBE_ATTEMPTS = 2


async def claim_probes(session: AsyncSession, limit: int = 5) -> list[ChannelCandidate]:
    """Pending candidates nobody has looked at yet.

    No lease: a probe is one public page fetch. Worst case two workers fetch the same
    page once each, which is cheaper than the bookkeeping to prevent it.
    """
    rows = list(
        await session.scalars(
            select(ChannelCandidate)
            .where(
                ChannelCandidate.status == "pending",
                ChannelCandidate.probed_at.is_(None),
                ChannelCandidate.probe_attempts < PROBE_ATTEMPTS,
            )
            .order_by(ChannelCandidate.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.probe_attempts += 1
    await session.flush()
    return rows


async def apply_probe(
    session: AsyncSession,
    candidate_id: int,
    *,
    title: str | None,
    subscribers: int | None,
    messages: int,
    audio: int,
    newest_msg_id: int | None,
    posts_per_day: float | None,
    unavailable: bool = False,
) -> ChannelCandidate | None:
    """Turns one probed page into the numbers the queue is sorted by."""
    candidate = await session.get(ChannelCandidate, candidate_id)
    if candidate is None:
        return None
    candidate.probed_at = datetime.now(UTC)
    if unavailable:
        # No public preview: it cannot be crawled, so it is not a candidate.
        candidate.status = "rejected"
        candidate.reject_reason = "preview_disabled"
        candidate.score = 0
        await session.flush()
        return candidate

    candidate.title = title or candidate.title
    candidate.subscribers = subscribers
    candidate.audio_ratio = (audio / messages) if messages else 0.0
    candidate.posts_per_day = posts_per_day
    # The newest message id is roughly how many posts the channel has ever made, so
    # the share that are music turns it into "about this many tracks".
    if newest_msg_id:
        candidate.tracks_estimate = int(newest_msg_id * (candidate.audio_ratio or 0.0))
    candidate.score = score_of(
        tracks_estimate=candidate.tracks_estimate,
        audio_ratio=candidate.audio_ratio,
        posts_per_day=candidate.posts_per_day,
        requesters=len(candidate.requested_by_user_ids),
        mentions=candidate.mention_count,
    )
    await session.flush()
    return candidate
