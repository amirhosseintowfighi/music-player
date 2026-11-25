-- 0023 — the bot's song of the day: an opt-in notification of its own kind.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system',
                    'new_release','alarm','daily_song'));
