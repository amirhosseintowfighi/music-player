-- 0022 — comments and reactions on a song.
--
-- Both hang off the root track (the song, not one channel's copy of it), so a song
-- posted in fifty channels has one conversation. A comment may point at a moment
-- in the song (at_second), SoundCloud-style.

CREATE TABLE IF NOT EXISTS track_comments (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    track_id   bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    user_id    bigint      NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    body       text        NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
    at_second  int         CHECK (at_second >= 0),
    hidden     boolean     NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS track_comments_track ON track_comments (track_id, created_at DESC, id DESC)
    WHERE NOT hidden;
CREATE INDEX IF NOT EXISTS track_comments_user ON track_comments (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS track_reactions (
    track_id   bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    user_id    bigint      NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    emoji      text        NOT NULL CHECK (emoji IN ('fire','heart','cry','love','party','clap')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (track_id, user_id, emoji)
);
