"""Admin API. Everything under ``/admin`` requires an admin token.

Impersonation mints a normal user token flagged read-only (``act_as_admin``), so an
admin can see exactly what a user sees without being able to change anything.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import RedisDep, SessionDep, SettingsDep, State, rate_limit_ip
from app.errors import Unauthorized
from app.schemas import (
    AdminBroadcastIn,
    AdminBroadcastOut,
    AdminLoginIn,
    AdminMeOut,
    AdminTokenOut,
    AdminUpsertIn,
    AuditEntryOut,
    BanIn,
    CandidateOut,
    CandidatePageOut,
    CrawlChannelOut,
    CrawlerHealthOut,
    FixMetadataIn,
    HealthOut,
    ImpersonateOut,
    ImportChannelsIn,
    ImportChannelsOut,
    MetadataReviewOut,
    OverviewOut,
    ParserHealthOut,
    PendingPaymentOut,
    PlanPatchIn,
    ProviderPatchIn,
    RejectCandidatesIn,
    ReportOut,
    ResolveReportIn,
    ResolverStatusOut,
    SettingIn,
    UserDetailOut,
    UserRowOut,
    UsersPageOut,
)
from app.security.adminauth import (
    AdminClaims,
    LoginError,
    decode_admin,
    encode_admin,
    verify_login_widget,
)
from app.security.tokens import TokenError
from app.services import admin as admin_service
from app.services import auth as auth_service
from app.services import broadcast as broadcast_service
from app.services import subscriptions
from tmusic_common.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])
_bearer = HTTPBearer(auto_error=False)

ADMIN_TOKEN_TTL_S = 8 * 3600


async def admin_claims(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AdminClaims:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("missing bearer token")
    try:
        return decode_admin(creds.credentials, settings.jwt_public_key, settings.jwt_issuer)
    except TokenError as exc:
        raise Unauthorized("invalid admin token") from exc


Claims = Annotated[AdminClaims, Depends(admin_claims)]
Limit = Annotated[int, Query(ge=1, le=200)]


# ── auth ──────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=AdminTokenOut, dependencies=[Depends(rate_limit_ip)])
async def login(body: AdminLoginIn, session: SessionDep, settings: SettingsDep) -> AdminTokenOut:
    """Telegram Login Widget → admin JWT. Not an admin row ⇒ 403, never a hint why."""
    try:
        payload = verify_login_widget(
            body.model_dump(exclude_none=True), settings.bot_token.get_secret_value()
        )
    except LoginError as exc:
        log.warning("admin.login_rejected", reason=str(exc))
        raise Unauthorized("login verification failed") from exc

    admin = await admin_service.login(session, payload.tg_id)
    permissions = admin_service.effective_permissions(admin)
    token, expires_at = encode_admin(
        AdminClaims(admin_id=admin.id, tg_id=admin.tg_id, role=admin.role, permissions=permissions),
        settings.jwt_private_key.get_secret_value(),
        settings.jwt_issuer,
        ADMIN_TOKEN_TTL_S,
    )
    log.info("admin.login", admin_id=admin.id, role=admin.role)
    return AdminTokenOut(
        access_token=token,
        expires_at=expires_at,
        me=AdminMeOut(
            id=admin.id,
            tg_id=admin.tg_id,
            role=admin.role,
            permissions=list(permissions),
            first_name=payload.first_name,
        ),
    )


@router.get("/me", response_model=AdminMeOut)
async def me(claims: Claims, session: SessionDep) -> AdminMeOut:
    admin = await admin_service.login(session, claims.tg_id)
    return AdminMeOut(
        id=admin.id,
        tg_id=admin.tg_id,
        role=admin.role,
        permissions=list(admin_service.effective_permissions(admin)),
        first_name="",
    )


# ── dashboards ────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewOut)
async def overview(claims: Claims, session: SessionDep) -> OverviewOut:
    admin_service.require(claims, "dashboard.view")
    data = await admin_service.overview(session)
    return OverviewOut(**asdict(data))


@router.get("/metrics/{metric}")
async def metrics(
    metric: Literal["users", "plays", "revenue"],
    claims: Claims,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[dict[str, Any]]:
    admin_service.require(claims, "dashboard.view")
    return await admin_service.timeseries(session, metric, days)


@router.get("/retention")
async def retention(
    claims: Claims, session: SessionDep, weeks: Annotated[int, Query(ge=2, le=26)] = 6
) -> list[dict[str, Any]]:
    admin_service.require(claims, "dashboard.view")
    return await admin_service.retention_cohorts(session, weeks)


# ── users ─────────────────────────────────────────────────────────────────────


@router.get("/users", response_model=UsersPageOut)
async def users(
    claims: Claims,
    session: SessionDep,
    q: str | None = None,
    plan: str | None = None,
    banned: bool | None = None,
    limit: Limit = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UsersPageOut:
    admin_service.require(claims, "users.view")
    rows, total = await admin_service.search_users(
        session, query=q, plan=plan, banned=banned, limit=limit, offset=offset
    )
    return UsersPageOut(items=[UserRowOut.model_validate(row) for row in rows], total=total)


@router.get("/users/{user_id}", response_model=UserDetailOut)
async def user_detail(user_id: int, claims: Claims, session: SessionDep) -> UserDetailOut:
    admin_service.require(claims, "users.view")
    data = await admin_service.user_detail(session, user_id)
    live = data["subscription"]
    return UserDetailOut(
        user=UserRowOut.model_validate(data["user"]),
        stats=data["stats"],
        subscription_status=live.status if live else "free",
        subscription_expires_at=live.expires_at if live else None,
    )


@router.post("/users/{user_id}/ban", response_model=UserRowOut)
async def ban(
    user_id: int, body: BanIn, claims: Claims, session: SessionDep, redis: RedisDep
) -> UserRowOut:
    user = await admin_service.set_ban(
        session, claims, user_id, body.banned, body.reason, redis=redis
    )
    return UserRowOut.model_validate(user)


@router.post("/users/{user_id}/gift", response_model=UserDetailOut)
async def gift(
    user_id: int,
    claims: Claims,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
    plan_code: str = "pro_monthly",
) -> UserDetailOut:
    admin_service.require(claims, "users.edit")
    await subscriptions.gift(session, claims.admin_id, user_id, days, plan_code)
    return await user_detail(user_id, claims, session)


@router.post("/users/{user_id}/impersonate", response_model=ImpersonateOut)
async def impersonate(
    user_id: int, claims: Claims, session: SessionDep, settings: SettingsDep
) -> ImpersonateOut:
    """Read-only token: any write endpoint refuses a token carrying ``act_as_admin``."""
    user = await admin_service.impersonation_window(session, claims, user_id)
    token, expires_at = await auth_service.impersonate(session, settings, user, claims.admin_id)
    return ImpersonateOut(access_token=token, expires_at=expires_at, user_id=user.id)


@router.get("/users.csv")
async def users_csv(claims: Claims, session: SessionDep) -> Response:
    admin_service.require(claims, "users.view")
    body = await admin_service.export_users_csv(session)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="users.csv"'},
    )


# ── payments ──────────────────────────────────────────────────────────────────


@router.get("/payments/pending", response_model=list[PendingPaymentOut])
async def pending(claims: Claims, session: SessionDep) -> list[PendingPaymentOut]:
    admin_service.require(claims, "payments.review")
    rows = await admin_service.pending_payments(session)
    return [PendingPaymentOut(**{**row, "public_id": str(row["public_id"])}) for row in rows]


@router.post("/payments/{payment_id}/approve")
async def approve(payment_id: int, claims: Claims, session: SessionDep) -> dict[str, str]:
    admin_service.require(claims, "payments.review")
    payment = await subscriptions.locked_payment(session, payment_id)
    await subscriptions.mark_paid(
        session, payment, provider_ref=payment.provider_ref, admin_id=claims.admin_id
    )
    return {"status": payment.status}


@router.post("/payments/{payment_id}/reject")
async def reject(
    payment_id: int, claims: Claims, session: SessionDep, reason: str = ""
) -> dict[str, str]:
    admin_service.require(claims, "payments.review")
    payment = await subscriptions.reject_payment(session, payment_id, claims.admin_id, reason)
    return {"status": payment.status}


@router.post("/payments/{payment_id}/refund")
async def refund(
    payment_id: int, claims: Claims, session: SessionDep, state: State, settings: SettingsDep
) -> dict[str, str]:
    admin_service.require(claims, "payments.refund")
    payment = await subscriptions.refund(
        session, state.http, settings, payment_id, admin_id=claims.admin_id
    )
    return {"status": payment.status}


# ── content ───────────────────────────────────────────────────────────────────


@router.post("/tracks/{track_id}/hide")
async def hide_track(
    track_id: int, claims: Claims, session: SessionDep, hidden: bool = True, reason: str = ""
) -> dict[str, bool]:
    track = await admin_service.set_track_hidden(session, claims, track_id, hidden, reason)
    return {"hidden": track.hidden}


@router.post("/channels/{channel_id}/status")
async def channel_status(
    channel_id: int, claims: Claims, session: SessionDep, status: str, reason: str = ""
) -> dict[str, str]:
    channel = await admin_service.set_channel_status(session, claims, channel_id, status, reason)
    return {"status": channel.status}


@router.post("/channels/{channel_id}/featured")
async def channel_featured(
    channel_id: int,
    claims: Claims,
    session: SessionDep,
    featured: bool = True,
    category_id: int | None = None,
) -> dict[str, bool]:
    channel = await admin_service.set_featured(session, claims, channel_id, featured, category_id)
    return {"is_featured": channel.is_featured}


@router.post("/blacklist")
async def blacklist(
    claims: Claims,
    session: SessionDep,
    entity_type: str,
    value: str,
    reason: str,
    report_id: int | None = None,
) -> dict[str, int]:
    entry = await admin_service.blacklist_add(
        session, claims, entity_type=entity_type, value=value, reason=reason, report_id=report_id
    )
    return {"id": entry.id}


@router.get("/reports", response_model=list[ReportOut])
async def reports(
    claims: Claims, session: SessionDep, status: str | None = "open", limit: Limit = 50
) -> list[ReportOut]:
    admin_service.require(claims, "content.view")
    rows = await admin_service.list_reports(session, status, limit)
    return [ReportOut.model_validate(row) for row in rows]


@router.post("/reports/{report_id}/resolve", response_model=ReportOut)
async def resolve_report(
    report_id: int, body: ResolveReportIn, claims: Claims, session: SessionDep
) -> ReportOut:
    report = await admin_service.resolve_report(
        session,
        claims,
        report_id,
        status=body.status,
        resolution=body.resolution,
        hide_entity=body.hide_entity,
    )
    return ReportOut.model_validate(report)


# ── broadcasts ────────────────────────────────────────────────────────────────


@router.post("/broadcasts", response_model=AdminBroadcastOut)
async def create_broadcast(
    body: AdminBroadcastIn, claims: Claims, session: SessionDep
) -> AdminBroadcastOut:
    broadcast = await broadcast_service.create(
        session,
        claims,
        target=body.target,
        variants=[variant.model_dump() for variant in body.variants],
        scheduled_at=body.scheduled_at,
    )
    return AdminBroadcastOut.model_validate(broadcast)


@router.get("/broadcasts", response_model=list[AdminBroadcastOut])
async def list_broadcasts(claims: Claims, session: SessionDep) -> list[AdminBroadcastOut]:
    admin_service.require(claims, "broadcast.send")
    rows = await broadcast_service.list_broadcasts(session)
    return [AdminBroadcastOut.model_validate(row) for row in rows]


@router.post("/broadcasts/estimate")
async def estimate_broadcast(
    body: dict[str, Any], claims: Claims, session: SessionDep
) -> dict[str, int]:
    admin_service.require(claims, "broadcast.send")
    return {"total": await broadcast_service.estimate(session, body)}


@router.post("/broadcasts/{broadcast_id}/status", response_model=AdminBroadcastOut)
async def broadcast_status(
    broadcast_id: int, claims: Claims, session: SessionDep, status: str
) -> AdminBroadcastOut:
    broadcast = await broadcast_service.set_status(session, claims, broadcast_id, status)
    return AdminBroadcastOut.model_validate(broadcast)


@router.get("/broadcasts/{broadcast_id}/progress")
async def broadcast_progress(
    broadcast_id: int, claims: Claims, session: SessionDep
) -> dict[str, Any]:
    admin_service.require(claims, "broadcast.send")
    return await broadcast_service.progress(session, broadcast_id)


# ── system ────────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthOut)
async def health(claims: Claims, session: SessionDep) -> HealthOut:
    admin_service.require(claims, "system.view")
    return HealthOut(**await admin_service.system_health(session))


@router.get("/audit", response_model=list[AuditEntryOut])
async def audit(
    claims: Claims,
    session: SessionDep,
    entity: str | None = None,
    actor_id: int | None = None,
    action: str | None = None,
    before_id: int | None = None,
    limit: Limit = 50,
) -> list[AuditEntryOut]:
    admin_service.require(claims, "system.view")
    rows = await admin_service.audit_page(
        session,
        entity=entity,
        actor_id=actor_id,
        action=action,
        before_id=before_id,
        limit=limit,
    )
    return [AuditEntryOut(**row) for row in rows]


@router.patch("/plans/{plan_code}")
async def patch_plan(
    plan_code: str, body: PlanPatchIn, claims: Claims, session: SessionDep
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    return await admin_service.update_plan(session, claims, plan_code, changes)


@router.put("/settings/{key}")
async def put_setting(
    key: str, body: SettingIn, claims: Claims, session: SessionDep
) -> dict[str, Any]:
    return await admin_service.set_setting(session, claims, key, body.value)


@router.put("/flags/{key}")
async def put_flag(
    key: str, body: SettingIn, claims: Claims, session: SessionDep
) -> dict[str, Any]:
    return await admin_service.set_flag(session, claims, key, body.value)


@router.patch("/providers/{code}")
async def patch_provider(
    code: str, body: ProviderPatchIn, claims: Claims, session: SessionDep
) -> dict[str, Any]:
    return await admin_service.set_provider(
        session, claims, code, is_enabled=body.is_enabled, config=body.config
    )


@router.get("/admins", response_model=list[AdminMeOut])
async def admins(claims: Claims, session: SessionDep) -> list[AdminMeOut]:
    admin_service.require(claims, "admins.manage")
    return [
        AdminMeOut(
            id=row.id,
            tg_id=row.tg_id,
            role=row.role,
            permissions=list(admin_service.effective_permissions(row)),
            first_name="",
            is_active=row.is_active,
        )
        for row in await admin_service.list_admins(session)
    ]


@router.put("/admins", response_model=AdminMeOut)
async def put_admin(body: AdminUpsertIn, claims: Claims, session: SessionDep) -> AdminMeOut:
    admin = await admin_service.upsert_admin(
        session,
        claims,
        tg_id=body.tg_id,
        role=body.role,
        permissions=body.permissions,
        is_active=body.is_active,
    )
    return AdminMeOut(
        id=admin.id,
        tg_id=admin.tg_id,
        role=admin.role,
        permissions=list(admin_service.effective_permissions(admin)),
        first_name="",
        is_active=admin.is_active,
    )


# ── channel discovery and crawler health (ADR-002 §5) ─────────────────────────


def _candidate(row: Any) -> CandidateOut:
    return CandidateOut(
        id=row.id,
        username=row.username,
        title=row.title,
        source=row.source,
        status=row.status,
        score=row.score,
        tracks_estimate=row.tracks_estimate,
        audio_ratio=row.audio_ratio,
        posts_per_day=row.posts_per_day,
        subscribers=row.subscribers,
        mention_count=row.mention_count,
        requested_by=len(row.requested_by_user_ids),
        discovered_from_channel_id=row.discovered_from_channel_id,
        probed_at=row.probed_at,
        reject_reason=row.reject_reason,
        created_at=row.created_at,
    )


@router.get("/candidates", response_model=CandidatePageOut)
async def candidates(
    claims: Claims,
    session: SessionDep,
    status: str = "pending",
    limit: Limit = 50,
    offset: int = 0,
) -> CandidatePageOut:
    """The review queue, best score first."""
    page = await admin_service.candidate_queue(
        session, claims, status=status, limit=limit, offset=offset
    )
    return CandidatePageOut(items=[_candidate(row) for row in page.items], total=page.total)


@router.post("/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: int, claims: Claims, session: SessionDep
) -> dict[str, int]:
    channel = await admin_service.approve_candidate(session, claims, candidate_id)
    return {"channel_id": channel.id}


@router.post("/candidates/reject")
async def reject_candidates(
    body: RejectCandidatesIn, claims: Claims, session: SessionDep
) -> dict[str, int]:
    """Bulk reject; every rejected username is blacklisted against re-discovery."""
    count = await admin_service.reject_candidates(session, claims, body.ids, body.reason)
    return {"rejected": count}


@router.post("/channels/import", response_model=ImportChannelsOut)
async def import_channels(
    body: ImportChannelsIn, claims: Claims, session: SessionDep
) -> ImportChannelsOut:
    result = await admin_service.import_channels(session, claims, body.text)
    return ImportChannelsOut(**result.as_dict())


@router.get("/crawler", response_model=CrawlerHealthOut)
async def crawler_health(claims: Claims, session: SessionDep) -> CrawlerHealthOut:
    return CrawlerHealthOut(**await admin_service.crawler_health(session, claims))


@router.get("/crawler/channels", response_model=list[CrawlChannelOut])
async def crawler_channels(
    claims: Claims, session: SessionDep, status: str | None = None, limit: Limit = 50
) -> list[CrawlChannelOut]:
    rows = await admin_service.crawler_channels(session, claims, status=status, limit=limit)
    return [CrawlChannelOut(**row) for row in rows]


@router.post("/crawler/channels/{channel_id}/recrawl")
async def recrawl(
    channel_id: int, claims: Claims, session: SessionDep, full: bool = False
) -> dict[str, str]:
    channel = await admin_service.recrawl(session, claims, channel_id, full=full)
    return {"crawl_status": channel.crawl_status, "status": channel.status}


@router.get("/crawler/parser", response_model=ParserHealthOut)
async def parser_health(claims: Claims, session: SessionDep, days: int = 14) -> ParserHealthOut:
    return ParserHealthOut(**await admin_service.parser_health(session, claims, days))


@router.get("/crawler/resolver", response_model=ResolverStatusOut)
async def resolver_status(claims: Claims, session: SessionDep) -> ResolverStatusOut:
    return ResolverStatusOut(**await admin_service.resolver_status(session, claims))


# ── metadata review queue (ADR-003 phase 13) ──────────────────────────────────


@router.get("/metadata/queue", response_model=list[MetadataReviewOut])
async def metadata_queue(
    claims: Claims,
    session: SessionDep,
    source: Literal["all", "reported", "unsure"] = "all",
    limit: Limit = 50,
) -> list[MetadataReviewOut]:
    rows = await admin_service.metadata_queue(session, claims, source=source, limit=limit)
    return [MetadataReviewOut(**row) for row in rows]


@router.post("/metadata/{track_id}/fix")
async def fix_metadata(
    track_id: int, body: FixMetadataIn, claims: Claims, session: SessionDep
) -> dict[str, int]:
    """Correct one track, or every track that shares its artist."""
    changed = await admin_service.fix_metadata(
        session,
        claims,
        track_id,
        title=body.title,
        artist=body.artist,
        apply_to_artist=body.apply_to_artist,
    )
    return {"tracks": changed}
