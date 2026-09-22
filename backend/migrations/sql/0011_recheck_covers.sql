-- 0011 — let the ID3 probe look again at tracks that show no cover.
--
-- The probe read the file's head for album/year/genre and threw away the answer to
-- "does this file carry its own artwork?", which lives in the same bytes. So a track
-- whose message had no Telegram thumbnail showed a blank square even when the mp3
-- had a cover inside it all along.
--
-- Clearing the probe timestamp for those tracks puts them back in the queue. The job
-- takes 50 an hour, so the backlog drains gently and costs the resolver account
-- nothing it was not already spending.
UPDATE tracks
   SET metadata_probed_at = NULL
 WHERE metadata_probed_at IS NOT NULL
   AND NOT has_thumb
   AND NOT hidden
   AND playable;
