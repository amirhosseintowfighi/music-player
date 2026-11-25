-- 0020 — wake-up alarms: the bot wakes you with a song.
--
-- A Mini App cannot run while it is closed, so the alarm is a Telegram message at
-- the chosen minute (with the phone's own notification sound) and a button that
-- opens the app already playing. Times are kept as the listener's local minute of
-- the day plus the offset they were set in; next_fire_at is the UTC instant the
-- worker looks for, recomputed after every ring.

CREATE TABLE IF NOT EXISTS alarms (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label             text        CHECK (length(label) <= 60),
    minute_of_day     smallint    NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    -- Monday = 1, Tuesday = 2, … Sunday = 64. 0 rings once and then switches off.
    days              smallint    NOT NULL DEFAULT 127 CHECK (days BETWEEN 0 AND 127),
    tz_offset_minutes int         NOT NULL CHECK (tz_offset_minutes BETWEEN -720 AND 840),
    source            text        NOT NULL DEFAULT 'likes'
                      CHECK (source IN ('likes','playlist','daylist','recent')),
    playlist_id       bigint      REFERENCES playlists(id) ON DELETE SET NULL,
    enabled           boolean     NOT NULL DEFAULT true,
    next_fire_at      timestamptz,
    last_fired_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alarms_user ON alarms (user_id, id);
CREATE INDEX IF NOT EXISTS alarms_due ON alarms (next_fire_at) WHERE enabled;

-- The wake-up message is a notification of its own kind (never held for quiet hours).
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system',
                    'new_release','alarm'));
