-- 0010 — the MTProto fallback for channels with no public web preview.
--
-- The crawler reads t.me/s/<name>. A large share of music channels have that
-- switched off, and for those the web path can never work — no proxy, no header, no
-- retry changes it. A logged-in user account reads the same channel over MTProto and
-- sees exactly what a person scrolling the channel sees.
--
-- Off by default, and it stays that way unless an operator turns it on, because it
-- carries a risk the web path does not: Telegram may limit or ban the account.
-- ADR-002 §6 has the reasoning; RUNBOOK has the operational half.
INSERT INTO feature_flags (key, value, description) VALUES
 ('mtproto_fallback', 'false',
  'Crawl channels with no public web preview using a logged-in account (separate from the resolver)')
ON CONFLICT (key) DO NOTHING;

-- A channel that already hit preview_disabled should be picked up by the fallback the
-- moment it is enabled, rather than waiting out its one-day backoff.
CREATE INDEX IF NOT EXISTS channels_mtproto_due
    ON channels (next_crawl_at)
    WHERE source_type = 'mtproto' AND crawl_status IN ('idle', 'error');
