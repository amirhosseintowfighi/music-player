"""User reports: wrong metadata, or content that should not be here.

Two audiences in one table. A copyright or abuse report is a legal clock (ADR-0011,
§12 of the architecture) and goes straight to the admin queue; a "wrong artist" report
is free labour from the people who know this music best, and feeds the metadata review
queue. Both are cheap to file and idempotent, because a user who reports the same
track twice means "still wrong", not "two problems".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.models import Report, Track

TrackReportReason = Literal["wrong_metadata", "copyright", "inappropriate", "broken"]

# Copyright has a 48-hour SLA; the rest is ordinary queue work.
SLA_HOURS: dict[str, int] = {"copyright": 48, "inappropriate": 72}
DEFAULT_SLA_HOURS = 168
# One open report per user per track: repeats are the same complaint.
MAX_OPEN_PER_USER = 20


async def report_track(
    session: AsyncSession,
    user_id: int,
    track_id: int,
    *,
    reason: TrackReportReason,
    details: str = "",
) -> Report:
    track = await session.get(Track, track_id)
    if track is None or track.hidden:
        raise NotFound("track not found")
    root = track.canonical_track_id or track.id

    existing = (
        await session.scalars(
            select(Report).where(
                Report.reporter_user_id == user_id,
                Report.entity_type == "track",
                Report.entity_id == root,
                Report.status == "open",
            )
        )
    ).first()
    if existing is not None:
        # Same person, same track, still open: keep the first one, refresh the detail.
        if details:
            existing.details = details[:2000]
        existing.reason = reason
        return existing

    report = Report(
        reporter_user_id=user_id,
        entity_type="track",
        entity_id=root,
        reason=reason,
        details=details[:2000] or None,
        due_at=datetime.now(UTC) + timedelta(hours=SLA_HOURS.get(reason, DEFAULT_SLA_HOURS)),
    )
    session.add(report)
    await session.flush()
    return report


async def open_reports_by(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Report)
            .where(Report.reporter_user_id == user_id, Report.status == "open")
        )
        or 0
    )
