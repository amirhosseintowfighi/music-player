"""Admin panel back end: permissions, dashboards, user and content management.

Two rules hold everywhere in this module:

- **Every mutation is audited.** ``audit_log`` is append-only at the database level,
  so an admin cannot erase what they did.
- **Permission is checked in the service, not only in the router.** The router is one
  more caller; the check that matters lives next to the action.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.text.artist_parser import REVIEW_THRESHOLD
from app.domain.text.normalizer import normalize_key
from app.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.models import AdminUser, Blacklist, Channel, Report, Track, User
from app.redis_util import BANNED_SET, resolve
from app.security.adminauth import AdminClaims
from app.security.password import hash_password, verify_password
from app.services import crawling, discovery, ingest, plans, resolving, subscriptions
from tmusic_common.logging import get_logger

log = get_logger(__name__)

# Fine-grained permissions. A role is only a convenient default set.
PERMISSIONS = (
    "dashboard.view",
    "users.view",
    "users.edit",
    "users.impersonate",
    "payments.view",
    "payments.review",
    "payments.refund",
    "content.view",
    "content.edit",
    "broadcast.send",
    "system.view",
    "system.edit",
    "admins.manage",
)

JSONB_PLAN_FIELDS = ("limits", "prices")

ROLE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "owner": ("*",),
    "admin": tuple(p for p in PERMISSIONS if p != "admins.manage"),
    "moderator": ("dashboard.view", "users.view", "content.view", "content.edit", "system.view"),
    "support": ("dashboard.view", "users.view", "payments.view", "payments.review"),
}


def has_permission(claims: AdminClaims, permission: str) -> bool:
    return "*" in claims.permissions or permission in claims.permissions


def require(claims: AdminClaims, permission: str) -> None:
    if not has_permission(claims, permission):
        raise Forbidden("missing permission", permission=permission)


async def audit(
    session: AsyncSession,
    claims: AdminClaims,
    action: str,
    entity: str,
    entity_id: str | int,
    payload: dict[str, Any] | None = None,
) -> None:
    await subscriptions.audit(
        session, "admin", claims.admin_id, action, entity, str(entity_id), payload or {}
    )


# ── admins ────────────────────────────────────────────────────────────────────


async def login(session: AsyncSession, tg_id: int) -> AdminUser:
    admin = (await session.scalars(select(AdminUser).where(AdminUser.tg_id == tg_id))).one_or_none()
    if admin is None or not admin.is_active:
        raise Forbidden("not an admin")
    return admin


async def login_with_password(session: AsyncSession, username: str, password: str) -> AdminUser:
    """Username + password → the admin row. One error for every failure mode.

    Distinguishing "no such user" from "wrong password" tells an attacker which half
    to keep guessing, so both take the same path — including the hash comparison,
    which runs even when the row is missing to keep the timing flat.
    """
    admin = (
        await session.scalars(
            select(AdminUser).where(
                func.lower(AdminUser.login_username) == username.strip().lower()
            )
        )
    ).one_or_none()
    stored = admin.password_hash if admin is not None else None
    ok = verify_password(password, stored)
    if admin is None or not ok or not admin.is_active:
        raise Forbidden("bad credentials")
    return admin


async def set_password(
    session: AsyncSession, tg_id: int, username: str, password: str
) -> AdminUser:
    """Give an existing admin a username and password. CLI-only; see ``app.cli``."""
    admin = (await session.scalars(select(AdminUser).where(AdminUser.tg_id == tg_id))).one_or_none()
    if admin is None:
        raise NotFound("not an admin")
    name = username.strip().lower()
    if len(name) < 3:
        raise InvalidInput("username must be at least 3 characters")
    taken = (
        await session.scalars(
            select(AdminUser).where(
                func.lower(AdminUser.login_username) == name, AdminUser.id != admin.id
            )
        )
    ).one_or_none()
    if taken is not None:
        raise Conflict("username already taken")
    if len(password) < 12:
        raise InvalidInput("password must be at least 12 characters")
    admin.login_username = name
    admin.password_hash = hash_password(password)
    admin.password_set_at = datetime.now(UTC)
    return admin


def effective_permissions(admin: AdminUser) -> tuple[str, ...]:
    """Row permissions win; an empty list falls back to the role's defaults."""
    if admin.permissions:
        return tuple(admin.permissions)
    return ROLE_DEFAULTS.get(admin.role, ())


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    rows = await session.scalars(select(AdminUser).order_by(AdminUser.id))
    return list(rows.all())


async def upsert_admin(
    session: AsyncSession,
    claims: AdminClaims,
    *,
    tg_id: int,
    role: str,
    permissions: list[str],
    is_active: bool = True,
) -> AdminUser:
    require(claims, "admins.manage")
    if role not in ROLE_DEFAULTS:
        raise InvalidInput("unknown role", role=role)
    unknown = [p for p in permissions if p != "*" and p not in PERMISSIONS]
    if unknown:
        raise InvalidInput("unknown permission", permissions=unknown)

    admin = (await session.scalars(select(AdminUser).where(AdminUser.tg_id == tg_id))).one_or_none()
    if admin is None:
        admin = AdminUser(tg_id=tg_id, role=role, permissions=permissions, is_active=is_active)
        session.add(admin)
        await session.flush()
    else:
        if admin.id == claims.admin_id and not is_active:
            raise Conflict("an admin cannot deactivate themselves")
        admin.role, admin.permissions, admin.is_active = role, permissions, is_active
    await audit(session, claims, "admin.upsert", "admin", admin.id, {"role": role})
    return admin


# ── dashboards ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Overview:
    dau: int
    wau: int
    mau: int
    new_users_today: int
    paying_users: int
    trials: int
    tracks: int
    channels: int
    plays_today: int
    revenue_30d: list[dict[str, Any]]
    conversion_pct: float
    churn_30d_pct: float


async def overview(session: AsyncSession) -> Overview:
    row = (
        await session.execute(
            text(
                """
        SELECT
          (SELECT count(*) FROM users WHERE last_seen_at > now() - interval '1 day')   AS dau,
          (SELECT count(*) FROM users WHERE last_seen_at > now() - interval '7 days')  AS wau,
          (SELECT count(*) FROM users WHERE last_seen_at > now() - interval '30 days') AS mau,
          (SELECT count(*) FROM users WHERE created_at > date_trunc('day', now()))     AS new_today,
          (SELECT count(*) FROM subscriptions WHERE status IN ('active','grace'))      AS paying,
          (SELECT count(*) FROM subscriptions WHERE status = 'trialing')               AS trials,
          (SELECT count(*) FROM tracks WHERE canonical_track_id IS NULL AND NOT hidden) AS tracks,
          (SELECT count(*) FROM channels WHERE status = 'active')                      AS channels,
          (SELECT count(*) FROM play_history WHERE played_at > date_trunc('day', now())) AS plays
        """
            )
        )
    ).one()

    # Grouped by currency as well as gateway: Stars and rial are different money and
    # must never be added together or formatted with the same unit.
    revenue_rows = await session.execute(
        text(
            "SELECT provider, currency, coalesce(sum(amount), 0)::bigint FROM payments"
            " WHERE status = 'paid' AND paid_at > now() - interval '30 days'"
            " GROUP BY 1, 2 ORDER BY 3 DESC"
        )
    )
    revenue = [
        {"provider": provider, "currency": currency, "amount": int(total)}
        for provider, currency, total in revenue_rows
    ]

    total_users = int(row.mau) or 0
    paying = int(row.paying)
    conversion = round(100.0 * paying / total_users, 2) if total_users else 0.0

    churn_row = (
        await session.execute(
            text(
                """
        SELECT
          count(*) FILTER (WHERE status = 'expired' AND grace_until > now() - interval '30 days')
            AS lost,
          count(*) FILTER (WHERE started_at < now() - interval '30 days') AS base
          FROM subscriptions
        """
            )
        )
    ).one()
    base = int(churn_row.base or 0)
    churn = round(100.0 * int(churn_row.lost or 0) / base, 2) if base else 0.0

    return Overview(
        dau=int(row.dau),
        wau=int(row.wau),
        mau=int(row.mau),
        new_users_today=int(row.new_today),
        paying_users=paying,
        trials=int(row.trials),
        tracks=int(row.tracks),
        channels=int(row.channels),
        plays_today=int(row.plays),
        revenue_30d=revenue,
        conversion_pct=conversion,
        churn_30d_pct=churn,
    )


async def timeseries(
    session: AsyncSession, metric: Literal["users", "plays", "revenue"], days: int = 30
) -> list[dict[str, Any]]:
    """One row per day, with zero-filled gaps so the chart has no holes."""
    queries = {
        "users": "SELECT date_trunc('day', created_at) AS d, count(*) AS v FROM users"
        " WHERE created_at > now() - make_interval(days => :days) GROUP BY 1",
        "plays": "SELECT date_trunc('day', played_at) AS d, count(*) AS v FROM play_history"
        " WHERE played_at > now() - make_interval(days => :days) GROUP BY 1",
        "revenue": "SELECT date_trunc('day', paid_at) AS d, sum(amount) AS v FROM payments"
        " WHERE status = 'paid' AND paid_at > now() - make_interval(days => :days) GROUP BY 1",
    }
    if metric not in queries:
        raise InvalidInput("unknown metric", metric=metric)
    rows = await session.execute(
        text(
            f"""
        WITH days AS (
            SELECT generate_series(
                date_trunc('day', now()) - make_interval(days => :days - 1),
                date_trunc('day', now()),
                interval '1 day'
            ) AS d
        ),
        data AS ({queries[metric]})
        SELECT days.d::date AS day, coalesce(data.v, 0)::bigint AS value
          FROM days LEFT JOIN data USING (d) ORDER BY 1
        """
        ).bindparams(days=days)
    )
    return [{"day": day.isoformat(), "value": int(value)} for day, value in rows]


async def retention_cohorts(session: AsyncSession, weeks: int = 6) -> list[dict[str, Any]]:
    """Weekly signup cohorts and how many came back in each following week."""
    rows = await session.execute(
        text(
            """
        WITH cohorts AS (
            SELECT id AS user_id, date_trunc('week', created_at) AS cohort
              FROM users
             WHERE created_at > now() - make_interval(weeks => :weeks)
        ),
        activity AS (
            SELECT DISTINCT c.cohort, c.user_id,
                   (extract(epoch FROM date_trunc('week', h.played_at) - c.cohort) / 604800)::int
                       AS week_offset
              FROM cohorts c
              JOIN play_history h ON h.user_id = c.user_id
        )
        SELECT c.cohort::date AS cohort,
               count(DISTINCT c.user_id) AS size,
               a.week_offset,
               count(DISTINCT a.user_id) AS retained
          FROM cohorts c
          LEFT JOIN activity a ON a.cohort = c.cohort
         GROUP BY 1, 3 ORDER BY 1, 3
        """
        ).bindparams(weeks=weeks)
    )
    cohorts: dict[str, dict[str, Any]] = {}
    for cohort, size, offset, retained in rows:
        key = cohort.isoformat()
        entry = cohorts.setdefault(key, {"cohort": key, "size": int(size), "weeks": {}})
        if offset is not None and offset >= 0:
            entry["weeks"][str(int(offset))] = int(retained)
    return list(cohorts.values())


# ── users ─────────────────────────────────────────────────────────────────────


async def search_users(
    session: AsyncSession,
    *,
    query: str | None = None,
    plan: str | None = None,
    banned: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[User], int]:
    clauses = ["true"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if query:
        cleaned = query.strip().lstrip("@")
        if cleaned.isdigit():
            clauses.append("(u.tg_id = :tg_id OR u.id = :tg_id)")
            params["tg_id"] = int(cleaned)
        else:
            clauses.append("(u.username ILIKE :q OR u.first_name ILIKE :q)")
            params["q"] = f"%{cleaned}%"
    if plan:
        clauses.append("u.plan_code = :plan")
        params["plan"] = plan
    if banned is not None:
        clauses.append("u.is_banned = :banned")
        params["banned"] = banned
    where = " AND ".join(clauses)

    total = await session.scalar(
        text(f"SELECT count(*) FROM users u WHERE {where}").bindparams(
            **{k: v for k, v in params.items() if k not in ("limit", "offset")}
        )
    )
    rows = await session.scalars(
        select(User).from_statement(
            text(
                f"SELECT u.* FROM users u WHERE {where}"
                " ORDER BY u.last_seen_at DESC LIMIT :limit OFFSET :offset"
            ).bindparams(**params)
        )
    )
    return list(rows.all()), int(total or 0)


async def user_detail(session: AsyncSession, user_id: int) -> dict[str, Any]:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    stats = (
        await session.execute(
            text(
                """
        SELECT
          (SELECT count(*) FROM user_channels WHERE user_id = :id)  AS channels,
          (SELECT count(*) FROM playlists WHERE user_id = :id)      AS playlists,
          (SELECT count(*) FROM likes WHERE user_id = :id)          AS likes,
          (SELECT count(*) FROM play_history WHERE user_id = :id)   AS plays,
          (SELECT coalesce(sum(amount), 0) FROM payments
            WHERE user_id = :id AND status = 'paid')                AS spent
        """
            ).bindparams(id=user_id)
        )
    ).one()
    live = await subscriptions.current(session, user_id)
    return {
        "user": user,
        "stats": {
            "channels": int(stats.channels),
            "playlists": int(stats.playlists),
            "likes": int(stats.likes),
            "plays": int(stats.plays),
            "spent": int(stats.spent),
        },
        "subscription": live,
    }


async def set_ban(
    session: AsyncSession,
    claims: AdminClaims,
    user_id: int,
    banned: bool,
    reason: str = "",
    redis: Redis | None = None,
) -> User:
    require(claims, "users.edit")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    user.is_banned = banned
    user.ban_reason = reason[:500] if banned else None
    if redis is not None:
        # The request path checks this set, so a ban takes effect on the next request
        # instead of waiting for the access token to expire.
        if banned:
            await resolve(redis.sadd(BANNED_SET, str(user_id)))
        else:
            await resolve(redis.srem(BANNED_SET, str(user_id)))
    await audit(
        session, claims, "user.ban" if banned else "user.unban", "user", user_id, {"reason": reason}
    )
    return user


async def export_users_csv(session: AsyncSession, limit: int = 10_000) -> str:
    rows = await session.execute(
        text(
            "SELECT id, tg_id, username, first_name, lang, plan_code, premium_until,"
            " is_banned, created_at, last_seen_at FROM users ORDER BY id LIMIT :limit"
        ).bindparams(limit=limit)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "tg_id",
            "username",
            "first_name",
            "lang",
            "plan",
            "premium_until",
            "banned",
            "created_at",
            "last_seen_at",
        ]
    )
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


# ── content ───────────────────────────────────────────────────────────────────


async def set_track_hidden(
    session: AsyncSession, claims: AdminClaims, track_id: int, hidden: bool, reason: str = ""
) -> Track:
    require(claims, "content.edit")
    track = await session.get(Track, track_id)
    if track is None:
        raise NotFound("track not found")
    track.hidden = hidden
    track.hidden_reason = reason[:200] if hidden else None
    await audit(
        session,
        claims,
        "track.hide" if hidden else "track.unhide",
        "track",
        track_id,
        {"reason": reason},
    )
    return track


async def set_channel_status(
    session: AsyncSession, claims: AdminClaims, channel_id: int, status: str, reason: str = ""
) -> Channel:
    require(claims, "content.edit")
    if status not in ("active", "paused", "blacklisted"):
        raise InvalidInput("unknown status", status=status)
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise NotFound("channel not found")
    channel.status = status
    channel.status_reason = reason[:200] or None
    await audit(session, claims, "channel.status", "channel", channel_id, {"status": status})
    return channel


async def set_featured(
    session: AsyncSession,
    claims: AdminClaims,
    channel_id: int,
    featured: bool,
    category_id: int | None = None,
) -> Channel:
    require(claims, "content.edit")
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise NotFound("channel not found")
    channel.is_featured = featured
    if category_id is not None:
        channel.category_id = category_id
    await audit(session, claims, "channel.featured", "channel", channel_id, {"featured": featured})
    return channel


async def blacklist_add(
    session: AsyncSession,
    claims: AdminClaims,
    *,
    entity_type: str,
    value: str,
    reason: str,
    report_id: int | None = None,
) -> Blacklist:
    """Blacklisting a channel also stops its indexing and hides its exclusive tracks."""
    require(claims, "content.edit")
    if entity_type not in ("channel", "artist", "track", "channel_username"):
        raise InvalidInput("unknown entity type", entity_type=entity_type)
    entry = Blacklist(
        entity_type=entity_type,
        value=value.strip().lower(),
        reason=reason[:500],
        report_id=report_id,
        created_by=claims.admin_id,
    )
    session.add(entry)
    try:
        await session.flush()
    except Exception as exc:  # already blacklisted
        raise Conflict("already blacklisted") from exc

    if entity_type in ("channel", "channel_username"):
        await session.execute(
            text(
                "UPDATE channels SET status = 'blacklisted', status_reason = :r"
                " WHERE username = :v OR tg_channel_id::text = :v"
            ).bindparams(r=reason[:200], v=entry.value)
        )
    await audit(session, claims, "blacklist.add", entity_type, value, {"reason": reason})
    return entry


async def list_reports(
    session: AsyncSession, status: str | None = "open", limit: int = 50
) -> list[Report]:
    stmt = select(Report).order_by(Report.due_at).limit(limit)
    if status:
        stmt = stmt.where(Report.status == status)
    rows = await session.scalars(stmt)
    return list(rows.all())


async def resolve_report(
    session: AsyncSession,
    claims: AdminClaims,
    report_id: int,
    *,
    status: str,
    resolution: str,
    hide_entity: bool = False,
) -> Report:
    require(claims, "content.edit")
    if status not in ("in_review", "actioned", "dismissed"):
        raise InvalidInput("unknown status", status=status)
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFound("report not found")
    report.status = status
    report.resolution = resolution[:500]
    report.handled_by = claims.admin_id
    report.resolved_at = datetime.now(UTC) if status != "in_review" else None

    if hide_entity and report.entity_type == "track":
        await set_track_hidden(session, claims, report.entity_id, True, resolution)
    if hide_entity and report.entity_type == "channel":
        await set_channel_status(session, claims, report.entity_id, "blacklisted", resolution)
    await audit(session, claims, "report.resolve", "report", report_id, {"status": status})
    return report


# ── plans, settings, flags ────────────────────────────────────────────────────


async def update_plan(
    session: AsyncSession, claims: AdminClaims, plan_code: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Plans are data, not code — price, limits and features are edited here."""
    require(claims, "system.edit")
    allowed = {
        "name_fa",
        "name_en",
        "period_days",
        "limits",
        "features",
        "prices",
        "is_active",
        "position",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidInput("unknown field", fields=sorted(unknown))
    if not changes:
        raise InvalidInput("nothing to change")

    sets = ", ".join(
        f"{key} = CAST(:{key} AS jsonb)" if key in JSONB_PLAN_FIELDS else f"{key} = :{key}"
        for key in changes
    )
    row = (
        await session.execute(
            text(
                f"UPDATE plans SET {sets}, updated_at = now() WHERE code = :code RETURNING code"
            ).bindparams(code=plan_code, **_jsonified(changes))
        )
    ).one_or_none()
    if row is None:
        raise NotFound("plan not found")
    plans.clear_caches()
    await audit(session, claims, "plan.update", "plan", plan_code, {"fields": sorted(changes)})
    return {"code": plan_code, **changes}


def _jsonified(changes: dict[str, Any]) -> dict[str, Any]:
    """jsonb and text[] columns need their Python values in the right shape."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        out[key] = json.dumps(value) if key in JSONB_PLAN_FIELDS else value
    return out


async def set_setting(
    session: AsyncSession, claims: AdminClaims, key: str, value: Any
) -> dict[str, Any]:
    require(claims, "system.edit")
    await session.execute(
        text(
            "INSERT INTO settings (key, value, updated_by, updated_at)"
            " VALUES (:k, CAST(:v AS jsonb), :admin, now())"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_by = excluded.updated_by, updated_at = now()"
        ).bindparams(k=key, v=json.dumps(value), admin=claims.admin_id)
    )
    await audit(session, claims, "setting.update", "setting", key, {"value": value})
    return {"key": key, "value": value}


async def set_flag(
    session: AsyncSession, claims: AdminClaims, key: str, value: Any
) -> dict[str, Any]:
    require(claims, "system.edit")
    row = (
        await session.execute(
            text(
                "UPDATE feature_flags SET value = CAST(:v AS jsonb), updated_at = now()"
                " WHERE key = :k RETURNING key"
            ).bindparams(k=key, v=json.dumps(value))
        )
    ).one_or_none()
    if row is None:
        raise NotFound("flag not found")
    plans.clear_caches()
    await audit(session, claims, "flag.update", "flag", key, {"value": value})
    return {"key": key, "value": value}


async def set_provider(
    session: AsyncSession,
    claims: AdminClaims,
    code: str,
    *,
    is_enabled: bool | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gateway switches and their non-secret config. Credentials stay in the env."""
    require(claims, "system.edit")
    sets, params = [], {"code": code}
    if is_enabled is not None:
        sets.append("is_enabled = :enabled")
        params["enabled"] = is_enabled  # type: ignore[assignment]
    if config is not None:
        if any("key" in k.lower() or "secret" in k.lower() or "token" in k.lower() for k in config):
            raise InvalidInput("credentials belong in the environment, not the database")
        sets.append("config = CAST(:config AS jsonb)")
        params["config"] = json.dumps(config)
    if not sets:
        raise InvalidInput("nothing to change")
    row = (
        await session.execute(
            text(
                f"UPDATE payment_providers SET {', '.join(sets)} WHERE code = :code RETURNING code"
            ).bindparams(**params)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("provider not found")
    await audit(session, claims, "provider.update", "provider", code, {"enabled": is_enabled})
    return {"code": code, "is_enabled": is_enabled}


# ── system ────────────────────────────────────────────────────────────────────


async def system_health(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
        SELECT
          (SELECT count(*) FROM indexer_accounts)                                   AS accounts,
          (SELECT count(*) FROM indexer_accounts WHERE status = 'healthy')          AS accounts_ok,
          (SELECT count(*) FROM edge_nodes WHERE is_enabled)                        AS edges,
          (SELECT count(*) FROM edge_nodes WHERE is_enabled AND healthy)            AS edges_ok,
          (SELECT count(*) FROM channels WHERE status = 'indexing')                 AS indexing,
          (SELECT count(*) FROM channels WHERE status = 'failed')                   AS failed,
          (SELECT count(*) FROM payments WHERE status = 'pending_review')           AS to_review,
          (SELECT count(*) FROM reports WHERE status = 'open')                      AS open_reports,
          (SELECT coalesce(sum(floodwait_24h_s), 0) FROM indexer_accounts)          AS floodwait_s
        """
            )
        )
    ).one()
    return {
        "indexer_accounts": {"total": int(row.accounts), "healthy": int(row.accounts_ok)},
        "edges": {"total": int(row.edges), "healthy": int(row.edges_ok)},
        "channels": {"indexing": int(row.indexing), "failed": int(row.failed)},
        "queues": {"payments_to_review": int(row.to_review), "open_reports": int(row.open_reports)},
        "floodwait_24h_s": int(row.floodwait_s),
    }


async def audit_page(
    session: AsyncSession,
    *,
    entity: str | None = None,
    actor_id: int | None = None,
    action: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict[str, Any]]:
    clauses = ["true"]
    params: dict[str, Any] = {"limit": limit}
    if entity:
        clauses.append("entity = :entity")
        params["entity"] = entity
    if actor_id is not None:
        clauses.append("actor_id = :actor_id")
        params["actor_id"] = actor_id
    if action:
        clauses.append("action = :action")
        params["action"] = action
    if before_id is not None:
        clauses.append("id < :before_id")
        params["before_id"] = before_id
    rows = await session.execute(
        text(
            f"SELECT id, actor_type, actor_id, action, entity, entity_id, payload, created_at"
            f" FROM audit_log WHERE {' AND '.join(clauses)}"
            f" ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(**params)
    )
    return [
        {
            "id": row.id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "action": row.action,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "payload": row.payload,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def pending_payments(session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT p.id, p.public_id, p.amount, p.currency, p.plan_code, p.receipt_file_id,"
            " p.review_due_at, p.created_at, u.id AS user_id, u.tg_id, u.first_name, u.username"
            " FROM payments p JOIN users u ON u.id = p.user_id"
            " WHERE p.status = 'pending_review' ORDER BY p.created_at LIMIT :limit"
        ).bindparams(limit=limit)
    )
    return [dict(row._mapping) for row in rows]


async def impersonation_window(session: AsyncSession, claims: AdminClaims, user_id: int) -> User:
    """Read-only impersonation. The token is minted by the auth service."""
    require(claims, "users.impersonate")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    await audit(session, claims, "user.impersonate", "user", user_id, {})
    log.warning("admin.impersonate", admin_id=claims.admin_id, user_id=user_id)
    return user


def since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


# ── crawler, candidates and resolver (ADR-002 §5) ─────────────────────────────


async def candidate_queue(
    session: AsyncSession, claims: AdminClaims, *, status: str = "pending", limit: int, offset: int
) -> discovery.QueuePage:
    require(claims, "content.view")
    return await discovery.queue(session, status=status, limit=limit, offset=offset)


async def approve_candidate(
    session: AsyncSession, claims: AdminClaims, candidate_id: int
) -> Channel:
    require(claims, "content.edit")
    channel = await discovery.approve(session, candidate_id, admin_id=claims.admin_id)
    await audit(
        session, claims, "candidate.approve", "candidate", candidate_id, {"channel": channel.id}
    )
    return channel


async def reject_candidates(
    session: AsyncSession, claims: AdminClaims, candidate_ids: list[int], reason: str
) -> int:
    """Bulk reject. Rejection blacklists the username so it is never suggested again."""
    require(claims, "content.edit")
    count = await discovery.reject(session, candidate_ids, reason=reason, admin_id=claims.admin_id)
    await audit(
        session,
        claims,
        "candidate.reject",
        "candidate",
        ",".join(str(i) for i in candidate_ids[:20]),
        {"count": count, "reason": reason},
    )
    return count


async def import_channels(
    session: AsyncSession, claims: AdminClaims, raw: str
) -> discovery.ImportResult:
    require(claims, "content.edit")
    result = await discovery.import_usernames(session, raw, admin_id=claims.admin_id)
    await audit(session, claims, "channels.import", "channel", "batch", result.as_dict())
    return result


async def plan_limits(session: AsyncSession, claims: AdminClaims) -> list[dict[str, Any]]:
    """Every plan's limits, so the panel can show what it is about to change."""
    require(claims, "system.view")
    rows = (await session.execute(text("SELECT code, limits FROM plans ORDER BY position"))).all()
    return [{"code": r.code, "limits": dict(r.limits or {})} for r in rows]


async def get_setting(session: AsyncSession, claims: AdminClaims, key: str) -> Any:
    require(claims, "system.view")
    row = await session.execute(text("SELECT value FROM settings WHERE key = :k").bindparams(k=key))
    found = row.first()
    return found[0] if found else None


async def crawler_health(session: AsyncSession, claims: AdminClaims) -> dict[str, Any]:
    require(claims, "system.view")
    return await crawling.health(session)


async def crawler_channels(
    session: AsyncSession, claims: AdminClaims, *, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Per-channel crawl state: progress, last success, last error, next visit."""
    require(claims, "system.view")
    clause = "WHERE crawl_status = :status" if status else ""
    rows = (
        await session.execute(
            text(
                f"""
        SELECT id, username, title, status, crawl_status, progress_pct,
               oldest_crawled_msg_id, newest_crawled_msg_id, tracks_count,
               last_crawl_at, next_crawl_at, crawl_interval_sec, fail_count,
               crawl_error, extraction_rate, preview_available
          FROM channels
          {clause}
         -- What is happening right now belongs at the top: a channel mid-crawl has an
         -- old last_crawl_at, so it used to fall past the 50-row cap and look gone.
         ORDER BY (crawl_status = 'running') DESC,
                  (crawl_status = 'error') DESC,
                  last_crawl_at DESC NULLS LAST
         LIMIT :limit
        """
            ).bindparams(limit=limit, **({"status": status} if status else {}))
        )
    ).mappings()
    return [dict(row) for row in rows]


async def recrawl(
    session: AsyncSession, claims: AdminClaims, channel_id: int, *, full: bool
) -> Channel:
    """Queue a channel for the crawler now — incrementally, or from the top again."""
    require(claims, "content.edit")
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise NotFound("channel not found")
    channel.crawl_status = "idle"
    channel.crawl_error = None
    channel.fail_count = 0
    channel.lease_owner = None
    channel.lease_until = None
    channel.next_crawl_at = datetime.now(UTC)
    if full:
        # Forget how far back we got; the backfill starts from the newest message
        # again. Nothing is deleted: re-ingesting a page updates the same rows.
        channel.oldest_crawled_msg_id = None
        channel.newest_crawled_msg_id = None
        channel.progress_pct = 0
        channel.status = "indexing"
    await audit(session, claims, "channel.recrawl", "channel", channel_id, {"full": full})
    return channel


async def parser_health(
    session: AsyncSession, claims: AdminClaims, days: int = 14
) -> dict[str, Any]:
    require(claims, "system.view")
    return await crawling.parser_health(session, days)


async def resolver_status(session: AsyncSession, claims: AdminClaims) -> dict[str, Any]:
    require(claims, "system.view")
    stats = dict(await resolving.stats(session))
    stats["accounts"] = await session.scalar(
        text("SELECT count(*) FROM indexer_accounts WHERE status = 'active'")
    )
    return stats


# ── metadata review queue (ADR-003 phase 13) ──────────────────────────────────
#
# Two kinds of doubt end up here: tracks the parser itself was unsure about
# (``metadata_confidence`` under the review threshold) and tracks a listener
# reported as wrong. Fixing one is cheap; the value is in fixing a *pattern*, so a
# correction can be applied to every track of the same artist at once.

REVIEW_PAGE = 50


async def metadata_queue(
    session: AsyncSession, claims: AdminClaims, *, source: str = "all", limit: int = REVIEW_PAGE
) -> list[dict[str, Any]]:
    """Tracks worth a human glance, worst first."""
    require(claims, "content.view")
    clause = {
        "reported": "t.id IN (SELECT entity_id FROM reports"
        " WHERE entity_type = 'track' AND status = 'open')",
        "unsure": "t.metadata_confidence < :threshold",
    }.get(
        source,
        "(t.metadata_confidence < :threshold OR t.id IN (SELECT entity_id FROM reports"
        " WHERE entity_type = 'track' AND status = 'open'))",
    )
    rows = (
        await session.execute(
            text(
                f"""
        SELECT t.id, t.title, t.metadata_confidence, t.file_name, t.album,
               COALESCE(string_agg(a.name, '، ' ORDER BY ta.position), '') AS artists,
               (SELECT count(*) FROM reports r
                 WHERE r.entity_type = 'track' AND r.entity_id = t.id AND r.status = 'open')
                 AS reports,
               (SELECT c.title FROM channel_tracks ct
                  JOIN channels c ON c.id = ct.channel_id
                 WHERE ct.track_id = t.id ORDER BY ct.posted_at LIMIT 1) AS channel_title
          FROM tracks t
          LEFT JOIN track_artists ta ON ta.track_id = t.id
          LEFT JOIN artists a ON a.id = ta.artist_id
         WHERE NOT t.hidden AND t.canonical_track_id IS NULL AND {clause}
         GROUP BY t.id
         ORDER BY reports DESC, t.metadata_confidence, t.id
         LIMIT :limit
        """
            ).bindparams(
                **({} if source == "reported" else {"threshold": REVIEW_THRESHOLD}),
                limit=limit,
            )
        )
    ).mappings()
    return [dict(row) for row in rows]


async def fix_metadata(
    session: AsyncSession,
    claims: AdminClaims,
    track_id: int,
    *,
    title: str | None = None,
    artist: str | None = None,
    apply_to_artist: bool = False,
) -> int:
    """Corrects one track, or every track that shares its (wrong) artist.

    Returns how many tracks were changed. The bulk form is the point: channels post
    the same mangled name hundreds of times, and fixing them one at a time is how a
    review queue becomes abandoned.
    """
    require(claims, "content.edit")
    track = await session.get(Track, track_id)
    if track is None:
        raise NotFound("track not found")

    changed = 1
    if title:
        track.title = title[:512]
        track.normalized_title = normalize_key(title)
    if artist:
        targets = [track_id]
        if apply_to_artist:
            targets = list(
                await session.scalars(
                    text(
                        "SELECT id FROM tracks WHERE normalized_artist = :key AND NOT hidden"
                    ).bindparams(key=track.normalized_artist)
                )
            )
            changed = len(targets)
        await ingest.set_artist(session, targets, artist)
    track.metadata_confidence = 100
    await session.execute(
        text(
            "UPDATE reports SET status = 'actioned', resolution = 'metadata fixed',"
            " handled_by = :admin WHERE entity_type = 'track' AND entity_id = :id"
            "   AND status = 'open'"
        ).bindparams(admin=claims.admin_id, id=track_id)
    )
    await audit(
        session,
        claims,
        "metadata.fix",
        "track",
        track_id,
        {"title": title, "artist": artist, "tracks": changed},
    )
    return changed
