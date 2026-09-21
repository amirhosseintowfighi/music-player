"""Phase 8: admin authentication, permissions, dashboards, moderation and broadcasts.

The security properties are the point of these tests: a user token must never open an
admin endpoint, a forged Login Widget payload must never mint one, a permission the
role does not have must be refused, and an impersonation token must be read-only.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.security.adminauth import AdminClaims, LoginError, verify_login_widget
from app.services import admin as admin_service
from app.services import broadcast as broadcast_service
from app.services import plans
from tests.conftest import BOT_TOKEN, bearer, login

OWNER_TG = 77001
SUPPORT_TG = 77002


def sign_login(fields: dict[str, Any], token: str = BOT_TOKEN) -> dict[str, Any]:
    payload = {k: v for k, v in fields.items() if v is not None}
    check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret = hashlib.sha256(token.encode()).digest()
    payload["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return payload


def login_payload(tg_id: int, **extra: Any) -> dict[str, Any]:
    return sign_login({"id": tg_id, "first_name": "Admin", "auth_date": int(time.time()), **extra})


async def make_admin(
    session: AsyncSession, tg_id: int, role: str = "owner", permissions: list[str] | None = None
) -> int:
    row = await session.execute(
        text(
            "INSERT INTO admin_users (tg_id, role, permissions, is_active)"
            " VALUES (:tg, :role, :perm, true) RETURNING id"
        ).bindparams(tg=tg_id, role=role, perm=permissions or [])
    )
    return int(row.scalar_one())


async def admin_token(client: httpx.AsyncClient, tg_id: int) -> dict[str, str]:
    resp = await client.post("/admin/login", json=login_payload(tg_id))
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── login widget ──────────────────────────────────────────────────────────────


def test_login_widget_signature_is_verified() -> None:
    payload = login_payload(1)
    assert verify_login_widget(payload, BOT_TOKEN).tg_id == 1

    tampered = {**payload, "id": 2}
    with pytest.raises(LoginError, match="signature"):
        verify_login_widget(tampered, BOT_TOKEN)

    with pytest.raises(LoginError, match="signature"):
        verify_login_widget(payload, "another:token")


def test_login_widget_rejects_a_replayed_payload() -> None:
    old = sign_login({"id": 1, "first_name": "A", "auth_date": int(time.time()) - 3600})
    with pytest.raises(LoginError, match="stale"):
        verify_login_widget(old, BOT_TOKEN)


def test_login_widget_requires_a_hash() -> None:
    with pytest.raises(LoginError, match="hash"):
        verify_login_widget({"id": 1, "auth_date": int(time.time())}, BOT_TOKEN)


async def test_login_rejects_a_non_admin(client: httpx.AsyncClient) -> None:
    resp = await client.post("/admin/login", json=login_payload(999999))
    assert resp.status_code == 403


async def test_login_rejects_a_deactivated_admin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.execute(text("UPDATE admin_users SET is_active = false"))
    await session.commit()
    resp = await client.post("/admin/login", json=login_payload(OWNER_TG))
    assert resp.status_code == 403


async def test_login_returns_role_defaults_when_no_explicit_permissions(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, SUPPORT_TG, role="support")
    await session.commit()
    resp = await client.post("/admin/login", json=login_payload(SUPPORT_TG))
    body = resp.json()
    assert body["me"]["role"] == "support"
    assert "payments.review" in body["me"]["permissions"]
    assert "users.edit" not in body["me"]["permissions"]


# ── token separation ──────────────────────────────────────────────────────────


async def test_a_user_token_cannot_open_an_admin_endpoint(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    user = await login(client, 60001)
    resp = await client.get("/admin/overview", headers=bearer(user))
    assert resp.status_code == 401


async def test_an_admin_token_cannot_open_a_user_endpoint(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    resp = await client.get("/v1/me", headers=headers)
    assert resp.status_code == 401


async def test_admin_endpoints_need_a_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/admin/overview")).status_code == 401


# ── permissions ───────────────────────────────────────────────────────────────


async def test_permission_is_enforced_per_endpoint(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, SUPPORT_TG, role="support")
    await session.commit()
    headers = await admin_token(client, SUPPORT_TG)

    assert (await client.get("/admin/overview", headers=headers)).status_code == 200
    assert (await client.get("/admin/users", headers=headers)).status_code == 200
    # support may look at users but not change them
    ban = await client.post(
        "/admin/users/1/ban", json={"banned": True, "reason": "x"}, headers=headers
    )
    assert ban.status_code == 403
    assert (await client.get("/admin/admins", headers=headers)).status_code == 403


def test_owner_has_every_permission() -> None:
    owner = AdminClaims(admin_id=1, tg_id=1, role="owner", permissions=("*",))
    for permission in admin_service.PERMISSIONS:
        assert admin_service.has_permission(owner, permission)


async def test_admins_can_be_managed_and_never_self_disabled(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    created = await client.put(
        "/admin/admins",
        json={"tg_id": 88123, "role": "moderator", "permissions": [], "is_active": True},
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["role"] == "moderator"

    bad = await client.put(
        "/admin/admins",
        json={"tg_id": 88124, "role": "moderator", "permissions": ["not.a.permission"]},
        headers=headers,
    )
    assert bad.status_code == 422

    me = await client.get("/admin/me", headers=headers)
    self_off = await client.put(
        "/admin/admins",
        json={"tg_id": OWNER_TG, "role": "owner", "permissions": ["*"], "is_active": False},
        headers=headers,
    )
    assert self_off.status_code == 409
    assert me.json()["role"] == "owner"


# ── dashboards ────────────────────────────────────────────────────────────────


async def test_overview_counts_real_rows(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    await login(client, 60002)  # one real user

    body = (await client.get("/admin/overview", headers=headers)).json()
    assert body["dau"] >= 1
    assert body["mau"] >= 1
    assert body["conversion_pct"] >= 0
    assert isinstance(body["revenue_30d"], list)


async def test_metrics_are_zero_filled(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    rows = (await client.get("/admin/metrics/users?days=7", headers=headers)).json()
    assert len(rows) == 7  # a quiet day is still a row
    assert all("day" in row and "value" in row for row in rows)

    bad = await client.get("/admin/metrics/nope", headers=headers)
    assert bad.status_code == 422


async def test_retention_returns_cohorts(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    await login(client, 60003)
    rows = (await client.get("/admin/retention?weeks=4", headers=headers)).json()
    assert isinstance(rows, list)


# ── users ─────────────────────────────────────────────────────────────────────


async def test_user_search_by_id_username_and_plan(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    token = await login(client, 60004)
    user_id = token["me"]["id"]
    await session.execute(
        text("UPDATE users SET username = 'sara_test' WHERE id = :id").bindparams(id=user_id)
    )
    await session.commit()

    by_tg = (await client.get("/admin/users?q=60004", headers=headers)).json()
    assert by_tg["total"] == 1
    by_name = (await client.get("/admin/users?q=sara", headers=headers)).json()
    assert by_name["total"] == 1
    by_plan = (await client.get("/admin/users?plan=pro_yearly", headers=headers)).json()
    assert by_plan["total"] == 0


async def test_ban_and_unban_are_audited(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_id = await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    token = await login(client, 60005)
    user_id = token["me"]["id"]

    banned = await client.post(
        f"/admin/users/{user_id}/ban", json={"banned": True, "reason": "spam"}, headers=headers
    )
    assert banned.json()["is_banned"] is True

    # A banned user cannot use their token any more.
    blocked = await client.get("/v1/me", headers=bearer(token))
    assert blocked.status_code in (401, 403)

    await client.post(
        f"/admin/users/{user_id}/ban", json={"banned": False, "reason": ""}, headers=headers
    )
    await session.rollback()
    rows = await session.execute(
        text(
            "SELECT action FROM audit_log WHERE actor_type = 'admin' AND actor_id = :a ORDER BY id"
        ).bindparams(a=admin_id)
    )
    actions = [row[0] for row in rows]
    assert "user.ban" in actions
    assert "user.unban" in actions


async def test_impersonation_token_is_read_only(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    token = await login(client, 60006)
    user_id = token["me"]["id"]

    resp = await client.post(f"/admin/users/{user_id}/impersonate", headers=headers)
    assert resp.status_code == 200, resp.text
    impersonated = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    assert (await client.get("/v1/me", headers=impersonated)).status_code == 200
    write = await client.put("/v1/tracks/1/like", headers=impersonated)
    assert write.status_code == 403


async def test_users_csv_export(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    await login(client, 60007)

    resp = await client.get("/admin/users.csv", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("id,tg_id,username")
    assert len(lines) >= 2


async def test_gift_activates_a_subscription(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    token = await login(client, 60008)
    user_id = token["me"]["id"]

    resp = await client.post(f"/admin/users/{user_id}/gift?days=14", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["subscription_status"] == "active"
    plans.clear_caches()


# ── content moderation ────────────────────────────────────────────────────────


async def test_hide_track_and_blacklist_channel(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    from app.services.ingest import ingest_items
    from tests.integration.helpers import item, make_channel

    await make_admin(session, OWNER_TG)
    channel = await make_channel(session, "badchannel")
    await ingest_items(session, channel, [item("آهنگ", "معین", msg=1)], bot_id=None)
    row = await session.execute(text("SELECT id FROM tracks LIMIT 1"))
    track_id = row.scalar_one()
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    hidden = await client.post(f"/admin/tracks/{track_id}/hide?reason=copyright", headers=headers)
    assert hidden.json()["hidden"] is True

    listed = await client.post(
        "/admin/blacklist?entity_type=channel&value=badchannel&reason=dmca", headers=headers
    )
    assert listed.status_code == 200
    await session.rollback()
    status = await session.execute(
        text("SELECT status FROM channels WHERE username = 'badchannel'")
    )
    assert status.scalar_one() == "blacklisted"

    duplicate = await client.post(
        "/admin/blacklist?entity_type=channel&value=badchannel&reason=dmca", headers=headers
    )
    assert duplicate.status_code == 409


async def test_reports_are_listed_and_resolved(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.execute(
        text(
            "INSERT INTO reports (entity_type, entity_id, reason, details, due_at)"
            " VALUES ('track', 1, 'copyright', 'mine', now() + interval '48 hours')"
        )
    )
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    rows = (await client.get("/admin/reports", headers=headers)).json()
    assert len(rows) == 1
    report_id = rows[0]["id"]

    resolved = await client.post(
        f"/admin/reports/{report_id}/resolve",
        json={"status": "dismissed", "resolution": "not infringing", "hide_entity": False},
        headers=headers,
    )
    assert resolved.json()["status"] == "dismissed"
    assert (await client.get("/admin/reports", headers=headers)).json() == []


# ── plans, settings, providers ────────────────────────────────────────────────


async def test_plan_price_is_editable_without_a_deploy(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    resp = await client.patch(
        "/admin/plans/pro_monthly", json={"prices": {"IRR": 1990000, "XTR": 180}}, headers=headers
    )
    assert resp.status_code == 200
    await session.rollback()
    row = await session.execute(text("SELECT prices FROM plans WHERE code = 'pro_monthly'"))
    assert row.scalar_one()["IRR"] == 1990000

    bad = await client.patch("/admin/plans/pro_monthly", json={"secret": 1}, headers=headers)
    assert bad.status_code == 422
    await session.execute(
        text(
            """UPDATE plans SET prices = '{"IRR": 1490000, "XTR": 150}' WHERE code='pro_monthly'"""
        )
    )
    await session.commit()
    plans.clear_caches()


async def test_provider_config_never_accepts_credentials(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    refused = await client.patch(
        "/admin/providers/zarinpal",
        json={"config": {"merchant_key": "secret-value"}},
        headers=headers,
    )
    assert refused.status_code == 422

    ok = await client.patch(
        "/admin/providers/zarinpal",
        json={"is_enabled": True, "config": {"sandbox": True}},
        headers=headers,
    )
    assert ok.status_code == 200
    await session.execute(text("UPDATE payment_providers SET is_enabled = (code = 'stars')"))
    await session.commit()


async def test_settings_and_flags_are_editable(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    assert (
        await client.put("/admin/settings/trial_days", json={"value": 14}, headers=headers)
    ).status_code == 200
    assert (
        await client.put("/admin/flags/ai_search", json={"value": True}, headers=headers)
    ).status_code == 200
    missing = await client.put("/admin/flags/nope", json={"value": True}, headers=headers)
    assert missing.status_code == 404

    await session.rollback()
    row = await session.execute(text("SELECT value FROM settings WHERE key = 'trial_days'"))
    assert row.scalar_one() in (14, "14")
    await session.execute(text("UPDATE settings SET value = '7' WHERE key = 'trial_days'"))
    await session.execute(text("UPDATE feature_flags SET value = 'false' WHERE key = 'ai_search'"))
    await session.commit()
    plans.clear_caches()


# ── system ────────────────────────────────────────────────────────────────────


async def test_health_and_audit_endpoints(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_id = await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)

    health = (await client.get("/admin/health", headers=headers)).json()
    assert "indexer_accounts" in health
    assert "queues" in health

    token = await login(client, 60009)
    await client.post(
        f"/admin/users/{token['me']['id']}/ban",
        json={"banned": True, "reason": "x"},
        headers=headers,
    )
    rows = (await client.get("/admin/audit?action=user.ban", headers=headers)).json()
    assert rows
    assert rows[0]["actor_id"] == admin_id
    assert rows[0]["action"] == "user.ban"


# ── broadcasts ────────────────────────────────────────────────────────────────


def test_segment_builder_rejects_unknown_filters() -> None:
    from app.errors import InvalidInput

    where, params = broadcast_service.build_segment({"plan": "free", "inactive_days": 30})
    assert ":plan" in where
    assert params == {"plan": "free", "inactive_days": 30}
    with pytest.raises(InvalidInput):
        broadcast_service.build_segment({"drop table": 1})


def test_variant_selection_is_stable_and_weighted() -> None:
    variants = broadcast_service.parse_variants(
        [{"text": "A", "weight": 1}, {"text": "B", "weight": 1}]
    )
    first = broadcast_service.pick_variant(variants, 42)
    assert broadcast_service.pick_variant(variants, 42) is first  # same user, same variant
    picks = {broadcast_service.pick_variant(variants, uid).text for uid in range(1, 50)}
    assert picks == {"A", "B"}  # both variants actually get used


async def test_broadcast_estimate_respects_segments(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    await login(client, 60010)
    await login(client, 60011)
    await session.execute(text("UPDATE users SET bot_blocked = true WHERE tg_id = 60011"))
    await session.commit()

    everyone = await client.post("/admin/broadcasts/estimate", json={}, headers=headers)
    assert everyone.json()["total"] == 1  # the blocked user is never counted

    free_only = await client.post(
        "/admin/broadcasts/estimate", json={"plan": "free"}, headers=headers
    )
    assert free_only.json()["total"] == 1


async def test_broadcast_lifecycle(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    await login(client, 60012)

    created = await client.post(
        "/admin/broadcasts",
        json={
            "target": {"plan": "free"},
            "variants": [{"text": "سلام", "button_text": "باز کن"}],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    broadcast_id = created.json()["id"]
    assert created.json()["total"] == 1

    paused = await client.post(
        f"/admin/broadcasts/{broadcast_id}/status?status=paused", headers=headers
    )
    assert paused.json()["status"] == "paused"

    progress = (
        await client.get(f"/admin/broadcasts/{broadcast_id}/progress", headers=headers)
    ).json()
    assert progress["percent"] == 0.0
    assert progress["total"] == 1


async def test_broadcast_send_loop_counts_blocked_users(
    session: AsyncSession, engine: Any, settings: Any
) -> None:
    """A user who blocked the bot is counted and flagged, never retried."""
    from aiogram import Bot
    from aiogram.exceptions import TelegramForbiddenError

    from app.db import make_sessionmaker
    from tests.integration.test_bot import RecordingSession

    class Blocking(RecordingSession):
        async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:  # noqa: ASYNC109
            self.calls.append(method)
            if len(self.calls) == 1:
                raise TelegramForbiddenError(method=method, message="blocked")
            return True

    admin_id = await make_admin(session, OWNER_TG)
    for tg_id in (60020, 60021):
        session.add(User(tg_id=tg_id, first_name="B", referral_code=f"r{tg_id}"))
    await session.flush()
    claims = AdminClaims(admin_id=admin_id, tg_id=OWNER_TG, role="owner", permissions=("*",))
    broadcast = await broadcast_service.create(
        session, claims, target={}, variants=[{"text": "hi"}]
    )
    broadcast.status = "scheduled"
    broadcast_id = broadcast.id
    await session.commit()

    recording = Blocking()
    bot = Bot(token=BOT_TOKEN, session=recording)
    broadcast_service.MESSAGES_PER_SECOND = 1000  # do not sleep through the test
    result = await broadcast_service.run(make_sessionmaker(engine), bot, settings, broadcast_id)
    await bot.session.close()

    assert result["blocked"] == 1
    assert result["sent"] == 1
    session.expire_all()
    row = await session.execute(
        text("SELECT status, sent, blocked FROM broadcasts WHERE id = :id").bindparams(
            id=broadcast_id
        )
    )
    status, sent, blocked = row.one()
    assert status == "completed"
    assert (sent, blocked) == (1, 1)
    flagged = await session.execute(
        text("SELECT count(*) FROM users WHERE bot_blocked AND tg_id IN (60020, 60021)")
    )
    assert flagged.scalar_one() == 1


async def test_a_paused_broadcast_sends_nothing(
    session: AsyncSession, engine: Any, settings: Any
) -> None:
    from aiogram import Bot

    from app.db import make_sessionmaker
    from tests.integration.test_bot import RecordingSession

    admin_id = await make_admin(session, OWNER_TG)
    session.add(User(tg_id=60030, first_name="B", referral_code="r60030"))
    await session.flush()
    claims = AdminClaims(admin_id=admin_id, tg_id=OWNER_TG, role="owner", permissions=("*",))
    broadcast = await broadcast_service.create(
        session, claims, target={}, variants=[{"text": "hi"}]
    )
    broadcast.status = "paused"
    broadcast_id = broadcast.id
    await session.commit()

    recording = RecordingSession()
    bot = Bot(token=BOT_TOKEN, session=recording)
    await broadcast_service.run(make_sessionmaker(engine), bot, settings, broadcast_id)
    await bot.session.close()
    assert recording.calls == []


async def test_run_broadcasts_job_picks_up_scheduled_work(
    session: AsyncSession, engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram import Bot

    from app.db import make_sessionmaker
    from app.workers import jobs
    from tests.integration.test_bot import RecordingSession

    admin_id = await make_admin(session, OWNER_TG)
    session.add(User(tg_id=60040, first_name="B", referral_code="r60040"))
    await session.flush()
    claims = AdminClaims(admin_id=admin_id, tg_id=OWNER_TG, role="owner", permissions=("*",))
    broadcast = await broadcast_service.create(
        session, claims, target={}, variants=[{"text": "سلام"}]
    )
    broadcast.status = "scheduled"
    await session.commit()

    recording = RecordingSession()
    monkeypatch.setattr(jobs, "build_bot", lambda settings: Bot(token=BOT_TOKEN, session=recording))
    broadcast_service.MESSAGES_PER_SECOND = 1000
    result = await jobs.run_broadcasts({"sessionmaker": make_sessionmaker(engine)})

    assert result == {"broadcasts": 1}
    assert len(recording.calls) == 1
    assert await jobs.run_broadcasts({"sessionmaker": make_sessionmaker(engine)}) == {
        "broadcasts": 0
    }  # nothing left to do
