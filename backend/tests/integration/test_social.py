"""Social: profiles, following, the friends feed and Wrapped.

Privacy is the thing worth testing here. A private profile must be invisible in every
direction — profile, follow, follower lists and feed — and the answer must never leak
that the account exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, InvalidInput, NotFound
from app.models import Channel, Track, User
from app.services import social
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe


async def make_user(session: AsyncSession, tg_id: int, **values: Any) -> User:
    user = User(tg_id=tg_id, first_name=f"U{tg_id}", referral_code=f"r{tg_id}", **values)
    session.add(user)
    await session.flush()
    return user


async def catalog(session: AsyncSession, count: int = 6) -> tuple[Channel, list[Track]]:
    channel = await make_channel(session, "socialch")
    artists = ["معین", "گوگوش", "هایده"]
    await ingest_items(
        session,
        channel,
        [
            item(f"آهنگ {index}", artists[index % 3], msg=8000 + index, duration=200 + index)
            for index in range(count)
        ],
        bot_id=None,
    )
    rows = await session.execute(text("SELECT id FROM tracks ORDER BY id"))
    ids = [row[0] for row in rows]
    tracks = [await session.get(Track, track_id) for track_id in ids]
    return channel, [t for t in tracks if t is not None]


async def play(
    session: AsyncSession,
    user_id: int,
    track_id: int,
    *,
    completed: bool = True,
    hours_ago: int = 1,
    year: int | None = None,
) -> None:
    when = (
        f"make_timestamptz({year}, 6, 15, 12, 0, 0)"
        if year
        else f"now() - make_interval(hours => {hours_ago})"
    )
    await session.execute(
        text(
            "INSERT INTO play_history (user_id, track_id, played_at, duration_played,"
            f" completed, source) VALUES (:u, :t, {when}, 240, :c, 'library')"
        ).bindparams(u=user_id, t=track_id, c=completed)
    )


# ── following ─────────────────────────────────────────────────────────────────


async def test_follow_and_unfollow_move_the_counters_once(session: AsyncSession) -> None:
    me = await make_user(session, 81001)
    them = await make_user(session, 81002)

    assert await social.follow(session, me.id, them.id) is True
    assert await social.follow(session, me.id, them.id) is False  # already following
    await session.refresh(me)
    await session.refresh(them)
    assert (me.following_count, them.followers_count) == (1, 1)

    assert await social.unfollow(session, me.id, them.id) is True
    assert await social.unfollow(session, me.id, them.id) is False
    await session.refresh(me)
    await session.refresh(them)
    assert (me.following_count, them.followers_count) == (0, 0)


async def test_you_cannot_follow_yourself(session: AsyncSession) -> None:
    me = await make_user(session, 81003)
    with pytest.raises(InvalidInput):
        await social.follow(session, me.id, me.id)


async def test_a_private_account_cannot_be_followed(session: AsyncSession) -> None:
    me = await make_user(session, 81004)
    hidden = await make_user(session, 81005, public_profile=False)
    with pytest.raises(NotFound):
        await social.follow(session, me.id, hidden.id)


async def test_following_has_a_ceiling(session: AsyncSession, monkeypatch: Any) -> None:
    monkeypatch.setattr(social, "MAX_FOLLOWING", 1)
    me = await make_user(session, 81006)
    first = await make_user(session, 81007)
    second = await make_user(session, 81008)
    await social.follow(session, me.id, first.id)
    with pytest.raises(Conflict):
        await social.follow(session, me.id, second.id)


# ── profiles ──────────────────────────────────────────────────────────────────


async def test_profile_shows_top_artists_and_public_playlists(session: AsyncSession) -> None:
    channel, tracks = await catalog(session)
    me = await make_user(session, 81010)
    await subscribe(session, me.id, channel.id)
    for track in tracks[:3]:
        await play(session, me.id, track.id)
    await session.execute(
        text(
            "INSERT INTO playlists (user_id, name, kind, is_public) VALUES (:u, 'عمومی', "
            "'manual', true), (:u, 'خصوصی', 'manual', false)"
        ).bindparams(u=me.id)
    )

    mine = await social.profile(session, me.id, me.id)
    assert mine.is_me is True
    assert mine.tracks_played == 3
    assert mine.top_artists
    assert len(mine.public_playlists) == 2  # my own private playlist is mine to see

    other = await make_user(session, 81011)
    theirs = await social.profile(session, me.id, other.id)
    assert theirs.is_me is False
    assert [entry["name"] for entry in theirs.public_playlists] == ["عمومی"]


async def test_a_private_profile_is_invisible_but_still_works_for_its_owner(
    session: AsyncSession,
) -> None:
    hidden = await make_user(session, 81012, public_profile=False)
    stranger = await make_user(session, 81013)

    assert (await social.profile(session, hidden.id, hidden.id)).is_me is True
    with pytest.raises(NotFound):
        await social.profile(session, hidden.id, stranger.id)


async def test_a_banned_account_disappears_from_profiles(session: AsyncSession) -> None:
    banned = await make_user(session, 81014, is_banned=True)
    stranger = await make_user(session, 81015)
    with pytest.raises(NotFound):
        await social.profile(session, banned.id, stranger.id)


async def test_connection_lists_hide_private_accounts(session: AsyncSession) -> None:
    star = await make_user(session, 81020)
    public_fan = await make_user(session, 81021)
    private_fan = await make_user(session, 81022)
    await social.follow(session, public_fan.id, star.id)
    await session.execute(
        text("INSERT INTO follows (follower_id, followee_id) VALUES (:a, :b)").bindparams(
            a=private_fan.id, b=star.id
        )
    )
    await session.execute(
        text("UPDATE users SET public_profile = false WHERE id = :id").bindparams(id=private_fan.id)
    )

    followers = await social.connections(session, star.id, star.id, direction="followers")
    assert [row["user_id"] for row in followers] == [public_fan.id]

    with pytest.raises(InvalidInput):
        await social.connections(session, star.id, star.id, direction="sideways")


# ── friends feed ──────────────────────────────────────────────────────────────


async def test_the_feed_only_shows_people_you_follow(session: AsyncSession) -> None:
    _channel, tracks = await catalog(session)
    me = await make_user(session, 81030)
    friend = await make_user(session, 81031)
    stranger = await make_user(session, 81032)
    await social.follow(session, me.id, friend.id)

    await play(session, friend.id, tracks[0].id)
    await play(session, stranger.id, tracks[1].id)

    feed = await social.friends_activity(session, me.id)
    assert [row["track_id"] for row in feed] == [tracks[0].id]


async def test_the_feed_ignores_skips_and_old_plays(session: AsyncSession) -> None:
    _channel, tracks = await catalog(session)
    me = await make_user(session, 81033)
    friend = await make_user(session, 81034)
    await social.follow(session, me.id, friend.id)

    await play(session, friend.id, tracks[0].id, completed=False)
    await play(session, friend.id, tracks[1].id, hours_ago=200)
    await play(session, friend.id, tracks[2].id, hours_ago=2)

    feed = await social.friends_activity(session, me.id)
    assert [row["track_id"] for row in feed] == [tracks[2].id]


async def test_going_private_removes_you_from_your_friends_feeds(
    session: AsyncSession,
) -> None:
    _channel, tracks = await catalog(session)
    me = await make_user(session, 81035)
    friend = await make_user(session, 81036)
    await social.follow(session, me.id, friend.id)
    await play(session, friend.id, tracks[0].id)
    assert await social.friends_activity(session, me.id)

    await social.set_public_profile(session, friend.id, False)
    assert await social.friends_activity(session, me.id) == []


# ── Wrapped ───────────────────────────────────────────────────────────────────


async def test_wrapped_summarises_the_year_and_is_stored(session: AsyncSession) -> None:
    _channel, tracks = await catalog(session)
    me = await make_user(session, 81040)
    year = datetime.now(UTC).year
    for track in tracks[:3]:
        await play(session, me.id, track.id, year=year)
        await play(session, me.id, track.id, year=year)

    payload = await social.wrapped(session, me.id)
    assert payload["year"] == year
    assert payload["plays"] == 6
    assert payload["minutes"] == 24  # 6 plays × 240 s
    assert payload["unique_tracks"] == 3
    assert payload["top_artists"]
    assert payload["busiest_day"]["plays"] == 6

    stored = await session.execute(
        text("SELECT payload FROM wrapped_reports WHERE user_id = :id").bindparams(id=me.id)
    )
    assert stored.scalar_one()["plays"] == 6

    # A second call reads the stored row rather than recomputing.
    await play(session, me.id, tracks[0].id, year=year)
    assert (await social.wrapped(session, me.id))["plays"] == 6
    assert (await social.wrapped(session, me.id, rebuild=True))["plays"] == 7


async def test_an_empty_year_is_not_stored(session: AsyncSession) -> None:
    me = await make_user(session, 81041)
    payload = await social.wrapped(session, me.id, 2021)
    assert payload["plays"] == 0
    rows = await session.execute(text("SELECT count(*) FROM wrapped_reports"))
    assert rows.scalar_one() == 0


# ── API ───────────────────────────────────────────────────────────────────────


async def test_profile_and_follow_endpoints(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _channel, tracks = await catalog(session)
    await session.commit()
    me = await login(client, 81050)
    them = await login(client, 81051)
    await play(session, them["me"]["id"], tracks[0].id)
    await session.commit()

    profile = await client.get(f"/v1/users/{them['me']['id']}/profile", headers=bearer(me))
    assert profile.status_code == 200
    assert profile.json()["is_following"] is False

    followed = await client.put(f"/v1/users/{them['me']['id']}/follow", headers=bearer(me))
    assert followed.json() == {"following": True, "changed": True}

    again = await client.get(f"/v1/users/{them['me']['id']}/profile", headers=bearer(me))
    assert again.json()["is_following"] is True
    assert again.json()["followers"] == 1

    feed = await client.get("/v1/social/feed", headers=bearer(me))
    assert [entry["track"]["id"] for entry in feed.json()] == [tracks[0].id]

    removed = await client.delete(f"/v1/users/{them['me']['id']}/follow", headers=bearer(me))
    assert removed.json() == {"following": False, "changed": True}


async def test_a_private_profile_is_404_over_the_api(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    me = await login(client, 81052)
    them = await login(client, 81053)
    await client.put("/v1/me/public-profile", json={"public": False}, headers=bearer(them))

    resp = await client.get(f"/v1/users/{them['me']['id']}/profile", headers=bearer(me))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"  # not "forbidden"


async def test_my_own_profile_endpoint(client: httpx.AsyncClient) -> None:
    me = await login(client, 81054)
    resp = await client.get("/v1/me/profile", headers=bearer(me))
    assert resp.status_code == 200
    assert resp.json()["is_me"] is True


async def test_wrapped_endpoint_hydrates_tracks(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel, tracks = await catalog(session)
    await session.commit()
    me = await login(client, 81055)
    await subscribe(session, me["me"]["id"], channel.id)
    await play(session, me["me"]["id"], tracks[0].id, year=datetime.now(UTC).year)
    await session.commit()

    resp = await client.get("/v1/me/wrapped", headers=bearer(me))
    assert resp.status_code == 200
    body = resp.json()
    assert body["plays"] == 1
    assert body["tracks"][0]["id"] == tracks[0].id


async def test_impersonation_cannot_follow(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A read-only admin token must not be able to act socially as the user."""
    from tests.integration.test_admin import OWNER_TG, admin_token, make_admin

    await make_admin(session, OWNER_TG)
    await session.commit()
    headers = await admin_token(client, OWNER_TG)
    victim = await login(client, 81056)
    target = await login(client, 81057)

    token = await client.post(f"/admin/users/{victim['me']['id']}/impersonate", headers=headers)
    impersonated = {"Authorization": f"Bearer {token.json()['access_token']}"}

    resp = await client.put(f"/v1/users/{target['me']['id']}/follow", headers=impersonated)
    assert resp.status_code == 403
