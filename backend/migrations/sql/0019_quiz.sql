-- 0019 — "Guess the song": a short clip, four titles, one is right.
--
-- A game is one row; its questions live in a small JSON array on it, because they
-- are only ever read and written together. Which choice is right stays on the
-- server until the player answers.

CREATE TABLE IF NOT EXISTS quiz_games (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source      text        NOT NULL CHECK (source IN ('library','catalog')),
    -- [{track_id, choices: [id, …], answered: id|null, correct: bool|null,
    --   clip_at: iso|null, points: int}]
    questions   jsonb       NOT NULL,
    score       int         NOT NULL DEFAULT 0,
    correct     smallint    NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS quiz_games_user ON quiz_games (user_id, created_at DESC);
-- The weekly leaderboard reads finished games of the last seven days.
CREATE INDEX IF NOT EXISTS quiz_games_finished ON quiz_games (finished_at DESC)
    WHERE finished_at IS NOT NULL;
