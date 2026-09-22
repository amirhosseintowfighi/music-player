"""The Telegram-free way into the panel.

The widget login ties the panel to one bot and to the single domain BotFather allows
per bot. These tests pin the second door: same token, same permissions, and no way to
learn from the error whether a username exists.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.password import generate_password, hash_password, verify_password
from app.services import admin as admin_service

from .test_admin import make_admin

PASSWORD = "correct-horse-battery-staple"


def test_a_hash_verifies_only_its_own_password() -> None:
    stored = hash_password(PASSWORD)
    assert stored != PASSWORD and stored.startswith("scrypt$")
    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "x", stored)
    assert not verify_password(PASSWORD, None)
    assert not verify_password(PASSWORD, "not-a-hash")
    # A fresh salt every time, so two admins with the same password do not match.
    assert hash_password(PASSWORD) != stored


def test_generated_passwords_are_long_and_unique() -> None:
    first, second = generate_password(), generate_password()
    assert first != second
    assert len(first) >= 12


async def test_password_login_returns_a_working_admin_token(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, 4242)
    await admin_service.set_password(session, 4242, "Operator", PASSWORD)
    await session.commit()

    resp = await client.post(
        "/admin/login/password", json={"username": "operator", "password": PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["me"]["role"] == "owner"

    me = await client.get("/admin/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["tg_id"] == 4242


async def test_a_wrong_password_and_an_unknown_user_look_identical(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, 4343)
    await admin_service.set_password(session, 4343, "someone", PASSWORD)
    await session.commit()

    wrong = await client.post(
        "/admin/login/password", json={"username": "someone", "password": "wrong-password"}
    )
    missing = await client.post(
        "/admin/login/password", json={"username": "nobody", "password": PASSWORD}
    )
    assert wrong.status_code == missing.status_code == 403
    assert wrong.json()["error"] == missing.json()["error"]


async def test_a_deactivated_admin_cannot_sign_in_with_a_password(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, 4444)
    await admin_service.set_password(session, 4444, "retired", PASSWORD)
    admin = await admin_service.login(session, 4444)
    admin.is_active = False
    await session.commit()

    resp = await client.post(
        "/admin/login/password", json={"username": "retired", "password": PASSWORD}
    )
    assert resp.status_code == 403


async def test_usernames_are_unique_and_case_insensitive(session: AsyncSession) -> None:
    await make_admin(session, 4545)
    await make_admin(session, 4546)
    await admin_service.set_password(session, 4545, "shared", PASSWORD)
    with pytest.raises(Exception, match="taken"):
        await admin_service.set_password(session, 4546, "SHARED", PASSWORD)


async def test_a_short_password_is_refused(session: AsyncSession) -> None:
    await make_admin(session, 4646)
    with pytest.raises(Exception, match="12 characters"):
        await admin_service.set_password(session, 4646, "shorty", "short")
