-- 0021 — listening streaks and badges.
--
-- Streaks are computed from play_history on request (a year of distinct days per
-- listener is a few hundred rows). Badges are remembered once earned, so the date
-- they were first earned stays put and "new" can be shown exactly once.

CREATE TABLE IF NOT EXISTS user_badges (
    user_id   bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge     text        NOT NULL CHECK (length(badge) BETWEEN 1 AND 40),
    earned_at timestamptz NOT NULL DEFAULT now(),
    seen      boolean     NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, badge)
);
