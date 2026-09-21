-- 0005 — channel discovery and parser health (ADR-002 §3, §5).
--
-- Two additions, both about *watching* the crawler rather than running it:
--
--   1. A candidate needs enough numbers for an admin to judge it at a glance —
--      how many audio posts, how often it posts — so the queue can be sorted by
--      something better than arrival order.
--   2. Telegram's preview HTML is an unofficial contract that will change without
--      warning. The only way to notice is to watch the share of messages a page
--      yields over time, so every crawled page updates a daily roll-up.

-- ── candidates: what a probe measured ───────────────────────────────────────
ALTER TABLE channel_candidates
    ADD COLUMN IF NOT EXISTS subscribers int,
    ADD COLUMN IF NOT EXISTS probed_at timestamptz,
    ADD COLUMN IF NOT EXISTS probe_attempts int NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS mention_count int NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS approved_channel_id bigint
        REFERENCES channels(id) ON DELETE SET NULL;

-- The probe queue: unprobed pending candidates, oldest first.
CREATE INDEX IF NOT EXISTS channel_candidates_to_probe
    ON channel_candidates (created_at)
    WHERE status = 'pending' AND probed_at IS NULL;

-- ── channels: last measured extraction rate ─────────────────────────────────
ALTER TABLE channels
    ADD COLUMN IF NOT EXISTS extraction_rate real;

-- ── parser health: one row per day ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawl_stats_daily (
    day             date        PRIMARY KEY,
    pages           int         NOT NULL DEFAULT 0,
    messages        int         NOT NULL DEFAULT 0,
    audio_items     int         NOT NULL DEFAULT 0,
    -- Pages that parsed but yielded nothing at all: the shape of a broken parser.
    empty_pages     int         NOT NULL DEFAULT 0,
    failures        int         NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
