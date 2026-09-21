from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInput
from app.models import Blacklist, Channel, User
from app.services.channels import ChannelRef, parse_channel_ref
from tests.conftest import bearer, login
from tests.integration.helpers import make_channel


@pytest.mark.parametrize(
    ("raw", "username"),
    [
        ("@PersianMusic", "PersianMusic"),
        ("PersianMusic", "PersianMusic"),
        ("t.me/PersianMusic", "PersianMusic"),
        ("https://t.me/PersianMusic/1234", "PersianMusic"),
        ("https://www.telegram.me/PersianMusic", "PersianMusic"),
        ("https://t.me/s/PersianMusic", "PersianMusic"),
        ("tg://resolve?domain=PersianMusic&post=4", "PersianMusic"),
        ("  @persian_music_2  ", "persian_music_2"),
    ],
)
def test_parse_channel_ref(raw: str, username: str) -> None:
    assert parse_channel_ref(raw) == ChannelRef(username=username)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("https://t.me/+AbCdEf123", "private_link"),
        ("https://t.me/joinchat/AbCdEf", "private_link"),
        ("https://t.me/c/123456/7", "private_link"),
        ("https://evil.example.com/PersianMusic", "not_telegram"),
        ("http://127.0.0.1/admin", "not_telegram"),
        ("https://t.me/", "invalid"),
        ("https://t.me/addstickers/pack", "invalid"),
        ("@ab", "invalid"),
        ("1channel", "invalid"),
        ("hello world", "invalid"),
    ],
)
def test_parse_channel_ref_rejects(raw: str, reason: str) -> None:
    with pytest.raises(InvalidInput) as info:
        parse_channel_ref(raw)
    assert info.value.details["reason"] == reason


async def test_add_list_remove_channel(client: httpx.AsyncClient, session: AsyncSession) -> None:
    auth = bearer(await login(client, 701))
    resp = await client.post("/v1/library/channels", json={"ref": "t.me/PopMusic"}, headers=auth)
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is True
    assert body["channel"]["status"] == "pending"
    assert body["channel"]["subscribers_count"] == 1
    cid = body["channel"]["id"]

    # Same channel, different spelling: no duplicate row, no double subscription.
    again = await client.post("/v1/library/channels", json={"ref": "@popmusic"}, headers=auth)
    assert again.json()["created"] is False
    assert again.json()["channel"]["subscribers_count"] == 1

    other = bearer(await login(client, 702))
    await client.post("/v1/library/channels", json={"ref": "popmusic"}, headers=other)
    listing = await client.get("/v1/library/channels", headers=auth)
    assert [c["id"] for c in listing.json()] == [cid]
    assert listing.json()[0]["subscribers_count"] == 2

    assert (await client.delete(f"/v1/library/channels/{cid}", headers=auth)).status_code == 204
    assert (await client.get("/v1/library/channels", headers=auth)).json() == []
    assert (await client.delete(f"/v1/library/channels/{cid}", headers=auth)).status_code == 404
    channel = await session.get(Channel, cid)
    assert channel is not None
    assert channel.subscribers_count == 1  # the channel itself stays (global entity)


async def test_free_plan_channel_limit(client: httpx.AsyncClient, session: AsyncSession) -> None:
    auth = bearer(await login(client, 703))
    for name in ("chanone", "chantwo", "chanthree"):
        assert (
            await client.post("/v1/library/channels", json={"ref": name}, headers=auth)
        ).status_code == 201
    over = await client.post("/v1/library/channels", json={"ref": "chanfour"}, headers=auth)
    assert over.status_code == 402
    assert over.json()["error"]["details"] == {"kind": "channels", "limit": 3}

    await session.execute(
        update(User)
        .where(User.tg_id == 703)
        .values(plan_code="pro_monthly", premium_until=datetime.now(UTC) + timedelta(days=1))
    )
    await session.commit()
    assert (
        await client.post("/v1/library/channels", json={"ref": "chanfour"}, headers=auth)
    ).status_code == 201


async def test_blacklisted_and_failed_channels(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 704))
    session.add(Blacklist(entity_type="channel_username", value="BadChan", reason="copyright"))
    failed = await make_channel(session, "retrychan", status="failed", status_reason="private")
    blocked = await make_channel(session, "blockedrow", status="blacklisted")
    await session.commit()
    failed_id, blocked_id = failed.id, blocked.id

    assert (
        await client.post("/v1/library/channels", json={"ref": "badchan"}, headers=auth)
    ).status_code == 403
    assert (
        await client.post("/v1/library/channels", json={"ref": "blockedrow"}, headers=auth)
    ).status_code == 403
    retry = await client.post("/v1/library/channels", json={"ref": "retrychan"}, headers=auth)
    assert retry.json()["channel"]["status"] == "pending"
    assert (await client.get(f"/v1/channels/{blocked_id}", headers=auth)).status_code == 404
    assert (await client.get(f"/v1/channels/{failed_id}", headers=auth)).status_code == 200

    bad = await client.post(
        "/v1/library/channels", json={"ref": "https://t.me/+secret"}, headers=auth
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["reason"] == "private_link"


async def test_featured_and_categories(client: httpx.AsyncClient, session: AsyncSession) -> None:
    auth = bearer(await login(client, 705))
    cats = (await client.get("/v1/channels/categories", headers=auth)).json()
    assert cats[0]["slug"] == "pop-fa"
    for i in range(3):
        await make_channel(session, f"feat{i}", is_featured=True, category_id=cats[0]["id"])
    await make_channel(session, "featother", is_featured=True, category_id=cats[1]["id"])
    await make_channel(session, "notfeat")
    await session.commit()

    page1 = (
        await client.get(f"/v1/channels/featured?category_id={cats[0]['id']}&limit=2", headers=auth)
    ).json()
    assert len(page1["items"]) == 2
    page2 = (
        await client.get(
            f"/v1/channels/featured?category_id={cats[0]['id']}&limit=2&cursor={page1['next_cursor']}",
            headers=auth,
        )
    ).json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None
    all_featured = (await client.get("/v1/channels/featured", headers=auth)).json()
    assert len(all_featured["items"]) == 4
    bad = await client.get("/v1/channels/featured?cursor=!!!", headers=auth)
    assert bad.status_code == 422


async def test_impersonation_tokens_cannot_write(client: httpx.AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    from app.security.tokens import AccessClaims, encode_access

    body = await login(client, 706)
    token, _ = encode_access(
        AccessClaims(body["me"]["id"], 706, "free", "fa", (), act_as_admin=1),
        settings.jwt_private_key.get_secret_value(),
        settings.jwt_issuer,
        60,
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/v1/library/channels", headers=headers)).status_code == 200
    resp = await client.post("/v1/library/channels", json={"ref": "somechan"}, headers=headers)
    assert resp.status_code == 403


async def test_unsubscribe_unknown_user_channel(session: AsyncSession) -> None:
    from app.errors import NotFound
    from app.services import channels

    with pytest.raises(NotFound):
        await channels.subscribe(session, 999_999, ChannelRef(username="whatever"))
    assert (await session.scalars(select(Channel))).all() == []
