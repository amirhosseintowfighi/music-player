"""Guess the song (0019): the answer stays hidden, clips are free, points are earned."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuizGame
from app.services import quiz
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe

SONGS = [
    "Ebi - Khaneh",
    "Ebi - Shabe Eshgh",
    "Googoosh - Pol",
    "Moein - Barooni",
    "Dariush - Booye Eydi",
    "Hayedeh - Soghati",
]


@pytest.fixture
async def player(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    body = await login(client, 960)
    channel = await make_channel(session, "quizchan")
    await ingest_items(
        session, channel, [item(title, msg=i + 1) for i, title in enumerate(SONGS)], bot_id=None
    )
    await subscribe(session, body["me"]["id"], channel.id)
    await session.execute(
        text("INSERT INTO edge_nodes (host) VALUES ('http://edge.test') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    return {"auth": bearer(body), "user_id": body["me"]["id"]}


async def answer_of(session: AsyncSession, game_id: int, index: int) -> int:
    game = await session.get(QuizGame, game_id)
    assert game is not None
    await session.refresh(game)
    return int(game.questions[index]["track_id"])


async def test_a_game_hides_its_answers_until_they_are_given(
    client: httpx.AsyncClient, player: dict[str, Any]
) -> None:
    resp = await client.post("/v1/quiz", json={}, headers=player["auth"])
    assert resp.status_code == 201, resp.text
    game = resp.json()
    # Six songs: six questions, four different titles each.
    assert len(game["questions"]) == len(SONGS)
    for question in game["questions"]:
        titles = [c["title"] for c in question["choices"]]
        assert len(set(titles)) == quiz.CHOICES
        assert question["answer_id"] is None
        assert question["answered"] is None
    assert "track_id" not in resp.text
    assert game["max_score"] == len(SONGS) * 200


async def test_clips_are_capped_and_do_not_spend_the_daily_limit(
    client: httpx.AsyncClient, session: AsyncSession, player: dict[str, Any]
) -> None:
    game = (await client.post("/v1/quiz", json={}, headers=player["auth"])).json()
    clip = await client.post(f"/v1/quiz/{game['id']}/questions/0/clip", headers=player["auth"])
    assert clip.status_code == 200, clip.text
    assert clip.json()["url"].startswith("http://edge.test/s/")
    plays = await session.scalar(
        text("SELECT count(*) FROM play_history WHERE user_id = :u").bindparams(u=player["user_id"])
    )
    assert plays == 0
    missing = await client.post(f"/v1/quiz/{game['id']}/questions/99/clip", headers=player["auth"])
    assert missing.status_code == 404


async def test_points_for_right_wrong_and_slow_answers(
    client: httpx.AsyncClient, session: AsyncSession, player: dict[str, Any]
) -> None:
    auth = player["auth"]
    game = (await client.post("/v1/quiz", json={}, headers=auth)).json()
    gid = game["id"]

    await client.post(f"/v1/quiz/{gid}/questions/0/clip", headers=auth)
    right = await answer_of(session, gid, 0)
    first = await client.post(
        f"/v1/quiz/{gid}/questions/0/answer", json={"choice_id": right}, headers=auth
    )
    body = first.json()
    assert body["correct"] is True
    assert body["answer_id"] == right
    assert body["answer"]["id"] == right
    assert 190 <= body["points"] <= 200  # answered at once: nearly all the speed points

    again = await client.post(
        f"/v1/quiz/{gid}/questions/0/answer", json={"choice_id": right}, headers=auth
    )
    assert again.status_code == 409

    right_1 = await answer_of(session, gid, 1)
    wrong_choice = next(c["id"] for c in game["questions"][1]["choices"] if c["id"] != right_1)
    wrong = await client.post(
        f"/v1/quiz/{gid}/questions/1/answer", json={"choice_id": wrong_choice}, headers=auth
    )
    assert wrong.json()["correct"] is False
    assert wrong.json()["points"] == 0

    stranger = await client.post(
        f"/v1/quiz/{gid}/questions/2/answer", json={"choice_id": 999999}, headers=auth
    )
    assert stranger.status_code == 422

    # Without a clip there is nothing to be fast about: base points only.
    plain = await client.post(
        f"/v1/quiz/{gid}/questions/2/answer",
        json={"choice_id": await answer_of(session, gid, 2)},
        headers=auth,
    )
    assert plain.json()["points"] == quiz.BASE_POINTS

    state = (await client.get(f"/v1/quiz/{gid}", headers=auth)).json()
    assert state["questions"][0]["answer_id"] == right
    assert state["questions"][3]["answer_id"] is None
    assert state["correct"] == 2
    assert state["finished"] is False


def test_speed_points_fall_off() -> None:
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    started = (now - timedelta(seconds=10)).isoformat()
    assert quiz.points_for(True, started, now) == 150
    assert quiz.points_for(True, (now - timedelta(minutes=5)).isoformat(), now) == 100
    assert quiz.points_for(False, started, now) == 0


def test_decoys_prefer_the_same_language() -> None:
    songs = [quiz._Song(i, f"t{i}", "fa" if i < 6 else "en") for i in range(12)]
    questions = quiz.build_questions(songs, random.Random(7))
    assert len(questions) == quiz.QUESTIONS
    for q in questions:
        langs = {"fa" if tid < 6 else "en" for tid in q["choices"]}
        assert len(langs) == 1
        assert q["track_id"] in q["choices"]


async def test_a_finished_game_reaches_the_leaderboards(
    client: httpx.AsyncClient, session: AsyncSession, player: dict[str, Any]
) -> None:
    auth = player["auth"]
    gid = (await client.post("/v1/quiz", json={}, headers=auth)).json()["id"]
    for index in range(len(SONGS)):
        resp = await client.post(
            f"/v1/quiz/{gid}/questions/{index}/answer",
            json={"choice_id": await answer_of(session, gid, index)},
            headers=auth,
        )
    assert resp.json()["finished"] is True
    assert resp.json()["score"] == len(SONGS) * quiz.BASE_POINTS

    friend = await login(client, 961)
    stranger = await login(client, 962)
    for other in (friend, stranger):
        await session.execute(
            text(
                "INSERT INTO quiz_games (user_id, source, questions, score, correct, finished_at) "
                "VALUES (:u, 'catalog', '[]', :s, 0, now())"
            ).bindparams(u=other["me"]["id"], s=900 if other is friend else 1500)
        )
    await session.execute(
        text("INSERT INTO follows (follower_id, followee_id) VALUES (:a, :b)").bindparams(
            a=player["user_id"], b=friend["me"]["id"]
        )
    )
    await session.commit()

    friends = (await client.get("/v1/quiz/board", headers=auth)).json()
    assert [e["user_id"] for e in friends["entries"]] == [friend["me"]["id"], player["user_id"]]
    assert friends["entries"][1]["is_me"] is True
    assert friends["my_best"] == len(SONGS) * quiz.BASE_POINTS
    assert friends["my_games"] == 1

    everyone = (await client.get("/v1/quiz/board?scope=global", headers=auth)).json()
    assert everyone["entries"][0]["user_id"] == stranger["me"]["id"]
    assert len(everyone["entries"]) == 3


async def test_too_few_songs_suggest_the_whole_catalogue(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 970))
    small = await client.post("/v1/quiz", json={}, headers=auth)
    assert small.status_code == 422
    assert small.json()["error"]["details"]["reason"] == "not_enough_songs"

    channel = await make_channel(session, "popchan")
    await ingest_items(session, channel, [item(t, msg=i + 1) for i, t in enumerate(SONGS)])
    await session.commit()
    catalog = await client.post("/v1/quiz", json={"source": "catalog"}, headers=auth)
    assert catalog.status_code == 201
    assert catalog.json()["source"] == "catalog"


async def test_games_are_private(client: httpx.AsyncClient, player: dict[str, Any]) -> None:
    gid = (await client.post("/v1/quiz", json={}, headers=player["auth"])).json()["id"]
    other = bearer(await login(client, 963))
    assert (await client.get(f"/v1/quiz/{gid}", headers=other)).status_code == 404
