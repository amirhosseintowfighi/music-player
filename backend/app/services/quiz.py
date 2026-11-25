"""Guess the song (0019): a few seconds of a song, four titles, one is right.

A game has ``QUESTIONS`` questions drawn from the listener's own channels (or, for
someone with too few songs, from what is popular). The right answer stays on the
server until the player picks: the client only ever sees four choices, in an order
that does not give it away.

The clip is a *prefetch* ticket (ADR-003 §2-3): it does not spend the daily play
limit and the edge serves only its first few hundred kilobytes — enough to guess
from, never the whole song.

Points: 100 for a right answer, plus up to 100 more for answering fast. "Fast" is
measured on the server, from the moment the clip was handed out, so a client cannot
claim it answered in no time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.errors import Conflict, InvalidInput, NotFound
from app.models import QuizGame

QUESTIONS = 10
CHOICES = 4
BASE_POINTS = 100
SPEED_POINTS = 100
# Answering within this long after the clip starts earns speed points, on a slope.
SPEED_WINDOW = timedelta(seconds=20)
POOL = 300
MIN_SONG_SECONDS = 30
LEADERBOARD_DAYS = 7
LEADERBOARD_SIZE = 20

_LIBRARY_POOL = text(
    """
    SELECT DISTINCT ON (r.normalized_title) r.id, r.normalized_title, r.language
      FROM user_channels uc
      JOIN channel_tracks ct ON ct.channel_id = uc.channel_id
      JOIN tracks t ON t.id = ct.track_id
      JOIN tracks r ON r.id = COALESCE(t.canonical_track_id, t.id)
     WHERE uc.user_id = :uid AND NOT r.hidden AND r.playable
       AND r.normalized_title <> '' AND r.duration >= :min_s
     ORDER BY r.normalized_title, random()
    """
)

_CATALOG_POOL = text(
    """
    SELECT id, normalized_title, language FROM (
        SELECT DISTINCT ON (r.normalized_title) r.id, r.normalized_title, r.language,
               r.plays_total
          FROM tracks r
         WHERE r.canonical_track_id IS NULL AND NOT r.hidden AND r.playable
           AND r.normalized_title <> '' AND r.duration >= :min_s
         ORDER BY r.normalized_title, r.plays_total DESC
    ) best
    ORDER BY plays_total DESC, id DESC
    LIMIT :pool
    """
)


@dataclass(frozen=True, slots=True)
class _Song:
    id: int
    title_key: str
    language: str | None


async def _pool(session: AsyncSession, user_id: int, source: str) -> list[_Song]:
    if source == "library":
        stmt = _LIBRARY_POOL.bindparams(uid=user_id, min_s=MIN_SONG_SECONDS)
    else:
        stmt = _CATALOG_POOL.bindparams(min_s=MIN_SONG_SECONDS, pool=POOL)
    rows = (await session.execute(stmt)).all()
    songs = [_Song(int(r.id), r.normalized_title, r.language) for r in rows]
    random.shuffle(songs)
    return songs[:POOL]


def build_questions(songs: list[_Song], rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Picks the answers and three wrong titles for each; prefers decoys in the same language.

    ``songs`` already has one song per title, so no question can offer the same title
    twice.
    """
    rng = rng or random.Random()  # noqa: S311 - a game, not security
    if len(songs) < CHOICES:
        return []
    answers = rng.sample(songs, min(QUESTIONS, len(songs)))
    questions: list[dict[str, Any]] = []
    for answer in answers:
        others = [s for s in songs if s.id != answer.id]
        same = [s for s in others if s.language and s.language == answer.language]
        pick_from = same if len(same) >= CHOICES - 1 else others
        decoys = rng.sample(pick_from, CHOICES - 1)
        choices = [answer.id, *(d.id for d in decoys)]
        rng.shuffle(choices)
        questions.append(
            {
                "track_id": answer.id,
                "choices": choices,
                "answered": None,
                "correct": None,
                "clip_at": None,
                "points": 0,
            }
        )
    return questions


async def start(session: AsyncSession, user_id: int, source: str) -> QuizGame:
    songs = await _pool(session, user_id, source)
    questions = build_questions(songs)
    if len(questions) < CHOICES:
        # Too few songs to make a game of (a new listener, a tiny library).
        raise InvalidInput("not enough songs for a game", reason="not_enough_songs")
    game = QuizGame(user_id=user_id, source=source, questions=questions)
    session.add(game)
    await session.flush()
    return game


async def get_game(session: AsyncSession, game_id: int, user_id: int) -> QuizGame:
    game = await session.get(QuizGame, game_id)
    if game is None or game.user_id != user_id:
        raise NotFound("game not found")
    return game


def _question(game: QuizGame, index: int) -> dict[str, Any]:
    if not 0 <= index < len(game.questions):
        raise NotFound("question not found")
    return game.questions[index]


def _save(game: QuizGame) -> None:
    # The questions were changed in place; JSONB has no change tracking of its own.
    flag_modified(game, "questions")


async def clip_track(session: AsyncSession, game: QuizGame, index: int) -> int:
    """The song to play for a question. Starts that question's clock the first time."""
    question = _question(game, index)
    if question["answered"] is not None:
        raise Conflict("question already answered", reason="answered")
    if question["clip_at"] is None:
        question["clip_at"] = datetime.now(UTC).isoformat()
        _save(game)
        await session.flush()
    return int(question["track_id"])


def points_for(correct: bool, clip_at: str | None, now: datetime) -> int:
    if not correct:
        return 0
    if clip_at is None:
        return BASE_POINTS
    elapsed = now - datetime.fromisoformat(clip_at)
    share = max(0.0, 1 - elapsed / SPEED_WINDOW)
    return BASE_POINTS + round(SPEED_POINTS * share)


@dataclass(frozen=True, slots=True)
class Answer:
    correct: bool
    answer_id: int
    points: int


async def answer(session: AsyncSession, game: QuizGame, index: int, choice_id: int) -> Answer:
    question = _question(game, index)
    if question["answered"] is not None:
        raise Conflict("question already answered", reason="answered")
    if choice_id not in question["choices"]:
        raise InvalidInput("not one of the choices", reason="bad_choice")
    correct = choice_id == question["track_id"]
    points = points_for(correct, question["clip_at"], datetime.now(UTC))
    question.update(answered=choice_id, correct=correct, points=points)
    _save(game)
    game.score += points
    game.correct += int(correct)
    if all(q["answered"] is not None for q in game.questions):
        game.finished_at = datetime.now(UTC)
    await session.flush()
    return Answer(correct=correct, answer_id=int(question["track_id"]), points=points)


@dataclass(frozen=True, slots=True)
class LeaderRow:
    user_id: int
    first_name: str
    username: str | None
    best_score: int
    games: int


async def leaderboard(session: AsyncSession, user_id: int, scope: str) -> list[LeaderRow]:
    """Best score per player over the last week: among the people you follow, or everyone."""
    # Global: the :uid test is always true, so both scopes bind the same parameters.
    circle = (
        "AND (g.user_id = :uid OR g.user_id IN "
        "(SELECT followee_id FROM follows WHERE follower_id = :uid))"
        if scope == "friends"
        else "AND :uid = :uid"
    )
    rows = await session.execute(
        text(
            "SELECT g.user_id, u.first_name, u.username, max(g.score) AS best, count(*) AS games "
            "FROM quiz_games g JOIN users u ON u.id = g.user_id "
            "WHERE g.finished_at > now() - :days "
            + circle
            + " GROUP BY g.user_id, u.first_name, u.username "
            "ORDER BY best DESC, games DESC, g.user_id LIMIT :lim"
        ).bindparams(uid=user_id, days=timedelta(days=LEADERBOARD_DAYS), lim=LEADERBOARD_SIZE)
    )
    return [
        LeaderRow(
            user_id=int(r.user_id),
            first_name=r.first_name or "",
            username=r.username,
            best_score=int(r.best),
            games=int(r.games),
        )
        for r in rows
    ]


@dataclass(frozen=True, slots=True)
class MyQuiz:
    games: int
    best_score: int
    week_best: int


async def my_stats(session: AsyncSession, user_id: int) -> MyQuiz:
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS games, COALESCE(max(score), 0) AS best, "
                "COALESCE(max(score) FILTER (WHERE finished_at > now() - :days), 0) AS week "
                "FROM quiz_games WHERE user_id = :uid AND finished_at IS NOT NULL"
            ).bindparams(uid=user_id, days=timedelta(days=LEADERBOARD_DAYS))
        )
    ).one()
    return MyQuiz(games=int(row.games), best_score=int(row.best), week_best=int(row.week))
