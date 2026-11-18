-- 0017 — what brings people in and keeps them: lyrics search, even loudness, and a
-- page for the people who run the channels.
--
-- Additive like 0015/0016: one new table and nullable / defaulted columns.

-- The words of a song, normalised the way search keys are (normalize_key), so a
-- line someone half-remembers finds the song. Filled whenever lyrics are stored.
ALTER TABLE track_lyrics ADD COLUMN IF NOT EXISTS search_text text;

-- How loud a track plays, in dB relative to full scale, as measured by the clients
-- that played it (the core never touches audio bytes). A running mean of up to a
-- handful of reports; the player turns everything towards one level from it.
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS loudness_db real;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS loudness_reports smallint NOT NULL DEFAULT 0;

-- The people who run a channel, proven by Telegram (an administrator of it, as seen
-- by the bot). They get a page of how their channel does in the app: aggregate
-- numbers only, never who listened.
CREATE TABLE IF NOT EXISTS channel_owners (
    channel_id  bigint      NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    user_id     bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    verified_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel_id, user_id)
);
CREATE INDEX IF NOT EXISTS channel_owners_user ON channel_owners (user_id);

-- AutoMix: how a song is built, for mixing one into the next like a DJ — tempo,
-- beat grid, key, where the intro ends and the outro starts. Measured by the first
-- clients that play it (the core never touches audio), kept as one small document.
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS mix_analysis jsonb;
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS mix_reports smallint NOT NULL DEFAULT 0;
