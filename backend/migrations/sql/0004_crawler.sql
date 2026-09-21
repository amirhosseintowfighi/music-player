-- 0004 — the web-preview crawler (ADR-002).
--
-- The catalogue stops coming from a pool of MTProto accounts and starts coming from
-- the public preview pages. Two consequences shape this migration:
--
--   1. A crawled track has no `file_unique_id` until someone actually plays it, so
--      that column becomes nullable and its unique index becomes partial. Identity
--      before resolve comes from `source_key` ("<channel_id>:<message_id>").
--   2. Playback needs something to play *now*, so a track carries the CDN url from
--      the preview page until the resolver has produced a real file id.
--
-- Everything is additive. The MTProto columns stay untouched so the old path keeps
-- working behind the feature flag until phase 6 removes it.

-- ── channels: crawl state ───────────────────────────────────────────────────
ALTER TABLE channels
    ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'web_preview'
        CHECK (source_type IN ('web_preview', 'bot_member', 'mtproto')),
    ADD COLUMN IF NOT EXISTS preview_available boolean,
    ADD COLUMN IF NOT EXISTS oldest_crawled_msg_id bigint,
    ADD COLUMN IF NOT EXISTS newest_crawled_msg_id bigint,
    ADD COLUMN IF NOT EXISTS last_crawl_at timestamptz,
    ADD COLUMN IF NOT EXISTS next_crawl_at timestamptz NOT NULL DEFAULT now(),
    -- Adaptive: a busy channel is re-read every 15 minutes, a quiet one once a day.
    ADD COLUMN IF NOT EXISTS crawl_interval_sec int NOT NULL DEFAULT 3600,
    ADD COLUMN IF NOT EXISTS crawl_status text NOT NULL DEFAULT 'idle'
        CHECK (crawl_status IN ('idle', 'running', 'error', 'preview_disabled', 'done')),
    ADD COLUMN IF NOT EXISTS crawl_error text;

-- Existing rows were built by the old path; say so rather than pretending.
UPDATE channels SET source_type = source WHERE source IN ('mtproto', 'bot_admin');
UPDATE channels SET source_type = 'bot_member' WHERE source = 'bot_admin';

-- The scheduler's only query: "what is due, most interesting first".
CREATE INDEX IF NOT EXISTS channels_crawl_due ON channels (next_crawl_at)
    WHERE crawl_status IN ('idle', 'error') AND status NOT IN ('blacklisted', 'paused');

-- ── tracks: identity before resolve, and something to play meanwhile ────────
ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS source_key text,
    ADD COLUMN IF NOT EXISTS cdn_url text,
    ADD COLUMN IF NOT EXISTS cdn_url_fetched_at timestamptz,
    ADD COLUMN IF NOT EXISTS resolve_status text NOT NULL DEFAULT 'resolved'
        CHECK (resolve_status IN ('unresolved', 'pending', 'resolved', 'failed')),
    ADD COLUMN IF NOT EXISTS resolve_attempts int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

-- Everything indexed so far came through MTProto and therefore already has a real
-- file_unique_id: those rows are resolved by definition.
UPDATE tracks SET resolved_at = created_at WHERE resolve_status = 'resolved';

-- file_unique_id is Telegram's stable id. A crawled track does not have one yet, so
-- the column becomes nullable and the uniqueness it guarantees becomes conditional.
ALTER TABLE tracks ALTER COLUMN file_unique_id DROP NOT NULL;
ALTER TABLE tracks DROP CONSTRAINT IF EXISTS tracks_file_unique_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS tracks_file_unique_id
    ON tracks (file_unique_id) WHERE file_unique_id IS NOT NULL;

-- Identity for a crawled track: "<channel_id>:<message_id>". Unique so a re-crawl
-- updates the same row instead of inserting a twin.
CREATE UNIQUE INDEX IF NOT EXISTS tracks_source_key
    ON tracks (source_key) WHERE source_key IS NOT NULL;

-- The resolver queue and the pre-warm job both read this.
CREATE INDEX IF NOT EXISTS tracks_unresolved ON tracks (resolve_status, id)
    WHERE resolve_status IN ('unresolved', 'pending');

-- A track must be addressable one way or the other; without this a row could exist
-- that nothing can ever play.
ALTER TABLE tracks DROP CONSTRAINT IF EXISTS tracks_identity_present;
ALTER TABLE tracks ADD CONSTRAINT tracks_identity_present
    CHECK (file_unique_id IS NOT NULL OR source_key IS NOT NULL);

-- ── channel discovery ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS channel_candidates (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username                 citext      NOT NULL UNIQUE,
    source                   text        NOT NULL
                             CHECK (source IN ('seed', 'user', 'crawl_mention', 'crawl_forward')),
    discovered_from_channel_id bigint    REFERENCES channels(id) ON DELETE SET NULL,
    requested_by_user_ids    bigint[]    NOT NULL DEFAULT '{}',
    title                    text,
    tracks_estimate          int,
    audio_ratio              real,
    posts_per_day            real,
    score                    real        NOT NULL DEFAULT 0,
    status                   text        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected', 'duplicate')),
    reviewed_by              bigint      REFERENCES admin_users(id) ON DELETE SET NULL,
    reviewed_at              timestamptz,
    reject_reason            text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

-- The admin queue: highest score first, and only what is still waiting.
CREATE INDEX IF NOT EXISTS channel_candidates_queue
    ON channel_candidates (score DESC, created_at) WHERE status = 'pending';

-- ── the switch ──────────────────────────────────────────────────────────────
INSERT INTO feature_flags (key, value, description) VALUES
 ('indexing_source', '"mtproto"',
  'Where the catalogue comes from: "mtproto" (old pool) or "crawler" (web preview, ADR-002)'),
 ('crawler_enabled', 'false', 'Master switch for the web-preview crawler'),
 ('lazy_resolve', 'false', 'Serve the CDN url while a file id is resolved in the background')
ON CONFLICT (key) DO NOTHING;
