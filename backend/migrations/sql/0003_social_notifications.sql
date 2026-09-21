-- 0003 — what the brief's §5 needs beyond phase 9: notification preferences,
-- profile counters and the indexes the social feed reads.

-- Per-user notification settings. One jsonb column instead of a settings table:
-- it is always read together with the user row and never queried on its own.
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs jsonb NOT NULL DEFAULT '{}';
-- Tehran is UTC+3:30; quiet hours are evaluated in the user's own offset.
ALTER TABLE users ADD COLUMN IF NOT EXISTS tz_offset_minutes int NOT NULL DEFAULT 210;
ALTER TABLE users ADD COLUMN IF NOT EXISTS followers_count int NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS following_count int NOT NULL DEFAULT 0;

-- "who follows me", which the reverse of the primary key cannot answer.
CREATE INDEX IF NOT EXISTS follows_followee ON follows (followee_id, created_at DESC);

-- The friends feed reads recent plays of the people you follow.
CREATE INDEX IF NOT EXISTS play_history_recent ON play_history (played_at DESC, user_id);

-- The notification worker claims due rows; everything else about the table is unused.
CREATE INDEX IF NOT EXISTS notifications_due
    ON notifications (send_after) WHERE status = 'queued';

-- Yearly "Wrapped", computed once per user per year and then read many times.
CREATE TABLE IF NOT EXISTS wrapped_reports (
    user_id     bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year        smallint    NOT NULL,
    payload     jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, year)
);

-- ID3 backfill: album/year/genre come from the file, not from Telegram. The timestamp
-- is what stops the job from re-reading a track that simply has no tags.
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS metadata_probed_at timestamptz;
CREATE INDEX IF NOT EXISTS tracks_needs_probe ON tracks (channels_count DESC, id)
    WHERE canonical_track_id IS NULL AND NOT hidden AND metadata_probed_at IS NULL;
