-- 0006 — playback infrastructure (ADR-003, phase 10).
--
-- Two changes, both about making the next failure obvious instead of subtle:
--
--   1. `tracks.file_id` is a *Bot API* file id: it only exists for files our own bot
--      has seen, it is per-bot, and the resolver can never produce one. The old name
--      invited the reading "resolved tracks have a file_id", which is false and led
--      to a wrong source-selection order. The column says what it is now.
--   2. With no CDN fallback (ADR-002 §8), the first play of a track depends on
--      MTProto. When that is unavailable we record the demand so the pre-warm job
--      resolves what people are actually asking for, first.

ALTER TABLE tracks RENAME COLUMN file_id TO bot_file_id;
ALTER TABLE tracks RENAME COLUMN file_id_updated_at TO bot_file_id_updated_at;

ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS resolve_requests int NOT NULL DEFAULT 0;

-- The pre-warm queue: what someone wanted and could not get, best first.
CREATE INDEX IF NOT EXISTS tracks_resolve_demand
    ON tracks (resolve_requests DESC, likes_count DESC)
    WHERE resolve_status = 'unresolved';
