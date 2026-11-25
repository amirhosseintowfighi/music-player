-- 0018 — smart playlists: a playlist made of rules instead of hand-picked songs.
--
-- The rules are a small JSON document (schemas.SmartRules). The songs they match are
-- written into playlist_tracks like any other playlist's, so playing, sharing and
-- counting work unchanged; the list is rebuilt when the rules change and whenever
-- it is opened after going stale (refreshed_at).

ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_kind_check;
ALTER TABLE playlists ADD CONSTRAINT playlists_kind_check
    CHECK (kind IN ('manual','smart_ai','discover_weekly','daily_mix','radio',
                    'release_radar','blend','daylist','smart'));

ALTER TABLE playlists ADD COLUMN IF NOT EXISTS rules jsonb;
ALTER TABLE playlists ADD COLUMN IF NOT EXISTS refreshed_at timestamptz;
ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_smart_has_rules;
ALTER TABLE playlists ADD CONSTRAINT playlists_smart_has_rules
    CHECK (kind <> 'smart' OR rules IS NOT NULL);
