-- 0012 — find channels by searching Telegram, instead of waiting to be told about one.
--
-- Discovery so far is passive: the crawler reads a page, notices a @mention, and
-- queues it. That only ever finds channels that other channels already link to, so
-- the catalogue grows outward from wherever it started.
--
-- A logged-in account can ask Telegram directly — for public posts that are music
-- (messages.searchGlobal + the music filter), and for channels whose name matches
-- (contacts.search). Both answer with channels, which is all the existing candidate
-- queue needs; the tracks then arrive through the crawler exactly as before.
--
-- Off by default: it uses the same account the MTProto crawl does, and that account
-- carries a ban risk the web path does not.
ALTER TABLE channel_candidates DROP CONSTRAINT IF EXISTS channel_candidates_source_check;
ALTER TABLE channel_candidates
    ADD CONSTRAINT channel_candidates_source_check
    CHECK (source IN ('seed', 'user', 'crawl_mention', 'crawl_forward', 'telegram_search'));

INSERT INTO feature_flags (key, value, description) VALUES
 ('telegram_search', 'false',
  'Look for channels by searching Telegram with the crawling account (ADR-002)')
ON CONFLICT (key) DO NOTHING;

-- What to search for. Music words in the languages the catalogue actually holds;
-- an operator edits this row to aim the search somewhere else.
INSERT INTO settings (key, value) VALUES
 ('music_search_terms',
  '["موزیک", "آهنگ جدید", "ریمیکس", "music", "new song", "remix", "اغنية", "طرب", "şarkı"]')
ON CONFLICT (key) DO NOTHING;
