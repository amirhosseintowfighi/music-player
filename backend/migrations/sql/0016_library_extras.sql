-- 0016 — the smaller things a listener does with their library.
--
-- Additive like 0015: one new table and nullable / defaulted columns.

-- Snooze: a hidden song can come back by itself. NULL keeps it hidden for good.
ALTER TABLE hidden_tracks ADD COLUMN IF NOT EXISTS until timestamptz;

-- "Exclude from your taste profile": plays from this playlist (a sleep playlist, a
-- kids' playlist) do not shape anything recommended to its owner.
ALTER TABLE playlists ADD COLUMN IF NOT EXISTS exclude_from_taste boolean NOT NULL DEFAULT false;

-- Pinned to the top of the library: up to four playlists or artists, like Spotify's.
CREATE TABLE IF NOT EXISTS library_pins (
    user_id   bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind      text        NOT NULL CHECK (kind IN ('playlist','artist')),
    ref_id    bigint      NOT NULL,
    pinned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind, ref_id)
);
