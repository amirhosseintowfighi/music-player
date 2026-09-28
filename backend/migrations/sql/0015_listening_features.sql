-- 0015 — the listening features people expect from a music app.
--
-- Everything here is additive: new tables, nullable columns, and wider CHECKs, so an
-- existing installation keeps working the moment it migrates.

-- Generated playlists of new kinds: an artist's essentials, new releases from the
-- artists you follow, a two-person Blend, and the playlist that changes through the day.
ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_kind_check;
ALTER TABLE playlists ADD CONSTRAINT playlists_kind_check
    CHECK (kind IN ('manual','smart_ai','discover_weekly','daily_mix','radio',
                    'release_radar','blend','daylist'));

-- Playlist folders: one level, like Spotify's, owned by one user.
CREATE TABLE IF NOT EXISTS playlist_folders (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text        NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS playlist_folders_user ON playlist_folders (user_id, id);
ALTER TABLE playlists
    ADD COLUMN IF NOT EXISTS folder_id bigint REFERENCES playlist_folders(id) ON DELETE SET NULL;

-- Following an artist: what Release Radar and the new-release notice are built from.
CREATE TABLE IF NOT EXISTS artist_follows (
    user_id    bigint      NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    artist_id  bigint      NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, artist_id)
);
CREATE INDEX IF NOT EXISTS artist_follows_artist ON artist_follows (artist_id);

-- Last.fm's view of an artist's most-played songs, kept for "This Is" playlists.
-- ``lastfm_top`` holds titles in Last.fm's order; matching to our tracks happens on read.
ALTER TABLE artists
    ADD COLUMN IF NOT EXISTS lastfm_top        jsonb,
    ADD COLUMN IF NOT EXISTS lastfm_fetched_at timestamptz;

-- "Hide this song": it leaves every recommendation the user sees.
CREATE TABLE IF NOT EXISTS hidden_tracks (
    user_id    bigint      NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id   bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, track_id)
);

-- Lyrics, fetched once per track. ``found = false`` remembers a miss so it is not
-- asked about again on every play.
CREATE TABLE IF NOT EXISTS track_lyrics (
    track_id   bigint      PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    found      boolean     NOT NULL,
    synced     text,
    plain      text,
    source     text,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

-- A Blend: two people, one playlist made from both of their tastes.
CREATE TABLE IF NOT EXISTS blends (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_a      bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b      bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_a  bigint      REFERENCES playlists(id) ON DELETE SET NULL,
    playlist_b  bigint      REFERENCES playlists(id) ON DELETE SET NULL,
    match_pct   int         NOT NULL DEFAULT 0 CHECK (match_pct BETWEEN 0 AND 100),
    created_at  timestamptz NOT NULL DEFAULT now(),
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (user_a < user_b),
    UNIQUE (user_a, user_b)
);

-- Private session: nothing is recorded to history or recommendations until then.
ALTER TABLE users ADD COLUMN IF NOT EXISTS private_until timestamptz;

-- Where a long track (a podcast episode, a DJ set) was left, per listener.
CREATE TABLE IF NOT EXISTS track_progress (
    user_id    bigint      NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id   bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position_s int         NOT NULL CHECK (position_s >= 0),
    finished   boolean     NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, track_id)
);
CREATE INDEX IF NOT EXISTS track_progress_recent ON track_progress (user_id, updated_at DESC);

-- A notice when an artist you follow gets a new track.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system',
                    'new_release'));
