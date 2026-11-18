"""Channel owners: proven through Telegram, shown aggregates only (0017)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember, TelegramMethod
from aiogram.types import ChatMemberMember, ChatMemberOwner
from aiogram.types import User as TgUser
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelOwner
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe


class MemberSession(BaseSession):
    """Answers getChatMember the way Telegram would for one scenario."""

    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer
        self.asked: list[GetChatMember] = []

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        assert isinstance(method, GetChatMember)
        self.asked.append(method)
        user = TgUser(id=method.user_id, is_bot=False, first_name="Owner")
        if self.answer == "owner":
            return ChatMemberOwner(user=user, is_anonymous=False)
        if self.answer == "member":
            return ChatMemberMember(user=user)
        raise TelegramBadRequest(method, "Bad Request: member list is inaccessible")

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass


def answering(client: httpx.AsyncClient, answer: str) -> MemberSession:
    session = MemberSession(answer)
    client.app.state.bot.session = session  # type: ignore[attr-defined]
    return session


@pytest.fixture
async def channel(session: AsyncSession) -> dict[str, Any]:
    ch = await make_channel(session, "ownedtunes", tg_channel_id=777001)
    await ingest_items(
        session,
        ch,
        [
            item("Ebi - Khaneh", msg=1, fuid="AgADow1"),
            item("Ebi - Shabe Eshgh", msg=2, fuid="AgADow2"),
        ],
    )
    await session.commit()
    rows = await session.execute(text("SELECT title, id FROM tracks WHERE title <> ''"))
    ids = {title: tid for title, tid in rows.tuples()}
    return {"id": ch.id, "tracks": ids}


async def test_an_admin_claims_and_sees_aggregates(
    client: httpx.AsyncClient, session: AsyncSession, channel: dict[str, Any]
) -> None:
    owner = await login(client, 8801)
    fan = await login(client, 8802)
    other = await login(client, 8803)
    await subscribe(session, fan["me"]["id"], channel["id"])
    khaneh, eshgh = channel["tracks"]["Khaneh"], channel["tracks"]["Shabe Eshgh"]
    plays = [(fan, khaneh)] * 3 + [(other, khaneh), (fan, eshgh)]
    for who, track in plays:
        await session.execute(
            text(
                "INSERT INTO play_history (user_id, track_id, played_at, duration_played,"
                " completed, skipped, source) VALUES (:u, :t, now(), 120, true, false, 'channel')"
            ).bindparams(u=who["me"]["id"], t=track)
        )
    await session.commit()

    asked = answering(client, "owner")
    resp = await client.post(
        "/v1/owner/channels", json={"channel": "@ownedtunes"}, headers=bearer(owner)
    )
    assert resp.status_code == 200, resp.text
    # Asked by the channel's Bot API id, for the person asking.
    assert (asked.asked[0].chat_id, asked.asked[0].user_id) == (-1000000777001, 8801)
    body = resp.json()
    assert (body["plays_7d"], body["listeners_7d"], body["plays_30d"]) == (5, 2, 5)
    assert body["minutes_30d"] == 10
    assert body["app_followers"] == 1
    assert len(body["daily"]) == 30 and body["daily"][-1] == 5
    assert [t["id"] for t in body["top_tracks"]] == [khaneh, eshgh]
    assert "listeners" not in str(body["top_tracks"])  # no who-listened, anywhere

    listed = await client.get("/v1/owner/channels", headers=bearer(owner))
    assert [c["id"] for c in listed.json()] == [channel["id"]]
    # Somebody else sees nothing.
    assert (await client.get("/v1/owner/channels", headers=bearer(fan))).json() == []


async def test_non_admins_and_unseen_channels_are_refused(
    client: httpx.AsyncClient, session: AsyncSession, channel: dict[str, Any]
) -> None:
    tokens = await login(client, 8811)
    answering(client, "member")
    resp = await client.post(
        "/v1/owner/channels", json={"channel": "ownedtunes"}, headers=bearer(tokens)
    )
    assert resp.status_code == 403
    answering(client, "inaccessible")
    resp = await client.post(
        "/v1/owner/channels", json={"channel": "t.me/ownedtunes"}, headers=bearer(tokens)
    )
    assert resp.status_code in (400, 422)
    assert resp.json()["error"]["details"]["reason"] == "bot_not_admin"
    assert (await session.scalars(select(ChannelOwner))).all() == []
