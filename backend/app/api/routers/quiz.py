"""Guess the song (0019): start a game, hear a clip, pick a title, climb the board."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.api.deps import Claims, HttpDep, RedisDep, SessionDep, SettingsDep, WritableClaims
from app.models import QuizGame
from app.schemas import (
    QuizAnswerIn,
    QuizAnswerOut,
    QuizBoardOut,
    QuizChoiceOut,
    QuizGameOut,
    QuizLeaderOut,
    QuizQuestionOut,
    QuizStartIn,
    StreamOut,
)
from app.services import library, quiz, stream
from app.services.library import Lang

router = APIRouter(prefix="/v1/quiz", tags=["quiz"])


async def _out(session: SessionDep, game: QuizGame, lang: Lang) -> QuizGameOut:
    ids = [tid for q in game.questions for tid in q["choices"]]
    tracks = {t.id: t for t in await library.hydrate_tracks(session, ids, lang)}
    questions = []
    for index, q in enumerate(game.questions):
        choices = [
            QuizChoiceOut(
                id=tid,
                title=tracks[tid].title if tid in tracks else "—",
                artist=", ".join(a.name for a in tracks[tid].artists) if tid in tracks else "",
            )
            for tid in q["choices"]
        ]
        answered = q["answered"] is not None
        questions.append(
            QuizQuestionOut(
                index=index,
                choices=choices,
                answered=q["answered"],
                # The right answer is only shown once the question is behind the player.
                answer_id=q["track_id"] if answered else None,
                correct=q["correct"],
                points=q["points"],
            )
        )
    return QuizGameOut(
        id=game.id,
        source="catalog" if game.source == "catalog" else "library",
        questions=questions,
        score=game.score,
        correct=game.correct,
        finished=game.finished_at is not None,
        max_score=len(game.questions) * (quiz.BASE_POINTS + quiz.SPEED_POINTS),
    )


@router.post("", response_model=QuizGameOut, status_code=201)
async def start_game(body: QuizStartIn, claims: WritableClaims, session: SessionDep) -> QuizGameOut:
    """A new game. ``not_enough_songs`` means the library is too small: try ``catalog``."""
    game = await quiz.start(session, claims.user_id, body.source)
    return await _out(session, game, claims.lang)


@router.get("/board", response_model=QuizBoardOut)
async def board(
    claims: Claims, session: SessionDep, scope: Literal["friends", "global"] = "friends"
) -> QuizBoardOut:
    """This week's best scores: you and the people you follow, or everyone."""
    rows = await quiz.leaderboard(session, claims.user_id, scope)
    mine = await quiz.my_stats(session, claims.user_id)
    return QuizBoardOut(
        scope=scope,
        entries=[
            QuizLeaderOut(
                rank=rank,
                user_id=row.user_id,
                first_name=row.first_name,
                username=row.username,
                best_score=row.best_score,
                games=row.games,
                is_me=row.user_id == claims.user_id,
            )
            for rank, row in enumerate(rows, start=1)
        ],
        my_games=mine.games,
        my_best=mine.best_score,
        my_week_best=mine.week_best,
    )


@router.get("/{game_id}", response_model=QuizGameOut)
async def get_game(game_id: int, claims: Claims, session: SessionDep) -> QuizGameOut:
    game = await quiz.get_game(session, game_id, claims.user_id)
    return await _out(session, game, claims.lang)


@router.post("/{game_id}/questions/{index}/clip", response_model=StreamOut)
async def question_clip(
    game_id: int,
    index: int,
    claims: WritableClaims,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    http: HttpDep,
) -> StreamOut:
    """The clip to guess from: a capped ticket that never spends the daily play limit."""
    game = await quiz.get_game(session, game_id, claims.user_id)
    track_id = await quiz.clip_track(session, game, index)
    return await stream.issue_ticket(
        session, redis, settings, claims, track_id, http, prefetch=True
    )


@router.post("/{game_id}/questions/{index}/answer", response_model=QuizAnswerOut)
async def answer_question(
    game_id: int, index: int, body: QuizAnswerIn, claims: WritableClaims, session: SessionDep
) -> QuizAnswerOut:
    game = await quiz.get_game(session, game_id, claims.user_id)
    result = await quiz.answer(session, game, index, body.choice_id)
    found = await library.hydrate_tracks(
        session, [result.answer_id], claims.lang, viewer_id=claims.user_id
    )
    return QuizAnswerOut(
        correct=result.correct,
        answer_id=result.answer_id,
        points=result.points,
        score=game.score,
        finished=game.finished_at is not None,
        answer=found[0] if found else None,
    )
