-- 0014 — artist pages: a photo, and enough to know who has one.
--
-- The catalogue knows an artist's name because it was parsed out of a title. That is
-- all it knows: no photo, no popularity, nothing to put on a page. Spotify has all of
-- it for anyone who has ever been released commercially, so an artist is matched once
-- and the answer is kept.
--
-- ``enriched_at`` is the queue: null means never looked at, and a date means looked
-- at — found or not, so a name Spotify does not know is not asked about every hour.
ALTER TABLE artists
    ADD COLUMN IF NOT EXISTS image_url   text,
    ADD COLUMN IF NOT EXISTS spotify_id  text,
    ADD COLUMN IF NOT EXISTS popularity  int,
    ADD COLUMN IF NOT EXISTS enriched_at timestamptz;

-- The queue the job reads: never-looked-at artists, the ones with most tracks first,
-- because those are the pages people actually open.
CREATE INDEX IF NOT EXISTS artists_enrich_queue
    ON artists (tracks_count DESC, id)
    WHERE enriched_at IS NULL AND merged_into_id IS NULL AND NOT hidden;
