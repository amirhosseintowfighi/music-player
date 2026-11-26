"""Comments and reactions on a song (0022): one conversation per song, no links."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track
from app.services import comments
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel


@pytest.fixture
async def song(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    one = await make_channel(session, "talkchan1")
    two = await make_channel(session, "talkchan2")
    await ingest_items(session, one, [item("Ebi - Khaneh", msg=1, fuid="AgADc1")])
    await ingest_items(session, two, [item("Ebi - Khaneh", msg=1, fuid="AgADc2")])
    await session.commit()
    ids = list((await session.scalars(select(Track.id).order_by(Track.id))).all())
    return {
        "ids": ids,
        "a": bearer(await login(client, 1020)),
        "b": bearer(await login(client, 1021)),
    }


async def test_comments_are_shared_by_every_copy_of_a_song(
    client: httpx.AsyncClient, song: dict[str, Any]
) -> None:
    first, copy = song["ids"]
    posted = await client.post(
        f"/v1/tracks/{first}/comments",
        json={"body": "  این تیکه  عالیه  ", "at_second": 75},
        headers=song["a"],
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["body"] == "این تیکه عالیه"
    assert posted.json()["mine"] is True
    await client.post(f"/v1/tracks/{copy}/comments", json={"body": "second"}, headers=song["b"])

    page = (await client.get(f"/v1/tracks/{copy}/comments", headers=song["a"])).json()
    assert page["total"] == 2
    assert [c["body"] for c in page["items"]] == ["second", "این تیکه عالیه"]
    assert [c["mine"] for c in page["items"]] == [False, True]
    assert page["items"][1]["at_second"] == 75

    paged = (await client.get(f"/v1/tracks/{first}/comments?limit=1", headers=song["a"])).json()
    assert len(paged["items"]) == 1
    rest = (
        await client.get(
            f"/v1/tracks/{first}/comments?limit=1&cursor={paged['next_cursor']}", headers=song["a"]
        )
    ).json()
    assert [c["body"] for c in rest["items"]] == ["این تیکه عالیه"]


async def test_links_bursts_and_deleting(client: httpx.AsyncClient, song: dict[str, Any]) -> None:
    tid = song["ids"][0]
    for spam in ("join https://t.me/x", "follow @spamchannel", "www.example.com"):
        resp = await client.post(
            f"/v1/tracks/{tid}/comments", json={"body": spam}, headers=song["a"]
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["reason"] == "no_links"

    ids = []
    for n in range(comments.BURST):
        resp = await client.post(
            f"/v1/tracks/{tid}/comments", json={"body": f"c{n}"}, headers=song["a"]
        )
        ids.append(resp.json()["id"])
    burst = await client.post(
        f"/v1/tracks/{tid}/comments", json={"body": "one more"}, headers=song["a"]
    )
    assert burst.status_code == 429

    assert (await client.delete(f"/v1/comments/{ids[0]}", headers=song["b"])).status_code == 403
    assert (await client.delete(f"/v1/comments/{ids[0]}", headers=song["a"])).status_code == 204
    page = (await client.get(f"/v1/tracks/{tid}/comments", headers=song["b"])).json()
    assert page["total"] == comments.BURST - 1


async def test_hidden_comments_disappear(
    client: httpx.AsyncClient, session: AsyncSession, song: dict[str, Any]
) -> None:
    tid = song["ids"][0]
    cid = (
        await client.post(f"/v1/tracks/{tid}/comments", json={"body": "bad"}, headers=song["a"])
    ).json()["id"]
    await comments.hide(session, cid)
    await session.commit()
    assert (await client.get(f"/v1/tracks/{tid}/comments", headers=song["b"])).json()["total"] == 0


async def test_reactions(client: httpx.AsyncClient, song: dict[str, Any]) -> None:
    first, copy = song["ids"]
    mine = (await client.put(f"/v1/tracks/{first}/reactions/fire", headers=song["a"])).json()
    assert mine["counts"]["fire"] == 1
    assert mine["mine"] == ["fire"]
    await client.put(f"/v1/tracks/{copy}/reactions/fire", headers=song["b"])
    await client.put(f"/v1/tracks/{copy}/reactions/fire", headers=song["b"])  # idempotent
    both = (await client.get(f"/v1/tracks/{copy}/reactions", headers=song["a"])).json()
    assert both["counts"]["fire"] == 2
    assert both["counts"]["clap"] == 0
    off = (await client.delete(f"/v1/tracks/{first}/reactions/fire", headers=song["a"])).json()
    assert off["counts"]["fire"] == 1
    assert off["mine"] == []
    bad = await client.put(f"/v1/tracks/{first}/reactions/poop", headers=song["a"])
    assert bad.status_code == 422


async def test_no_comments_on_a_missing_song(
    client: httpx.AsyncClient, song: dict[str, Any]
) -> None:
    resp = await client.get("/v1/tracks/999999/comments", headers=song["a"])
    assert resp.status_code == 404


async def test_moderators_see_and_hide_comments(
    client: httpx.AsyncClient, session: AsyncSession, song: dict[str, Any]
) -> None:
    from tests.integration.test_admin import OWNER_TG, admin_token, make_admin

    tid = song["ids"][0]
    cid = (
        await client.post(f"/v1/tracks/{tid}/comments", json={"body": "rude"}, headers=song["a"])
    ).json()["id"]
    await make_admin(session, OWNER_TG)
    await session.commit()
    admin = await admin_token(client, OWNER_TG)
    listed = (await client.get("/admin/comments", headers=admin)).json()
    assert [c["id"] for c in listed] == [cid]
    assert listed[0]["title"] == "Khaneh"
    assert (await client.post(f"/admin/comments/{cid}/hide", headers=admin)).json() == {
        "hidden": True
    }
    assert (await client.get(f"/v1/tracks/{tid}/comments", headers=song["b"])).json()["total"] == 0
    audited = await session.scalar(
        text("SELECT count(*) FROM audit_log WHERE action = 'comment.hide'")
    )
    assert audited == 1
