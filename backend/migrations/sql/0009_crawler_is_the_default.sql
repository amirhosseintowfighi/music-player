-- 0009 — point the indexing switch at the crawler, because nothing else is left.
--
-- 0004 seeded `indexing_source = "mtproto"` while the MTProto pool was still the
-- catalogue's owner and the crawler was the experiment. Phase 6 deleted that pool
-- (indexer/pool.py, indexer/worker.py, app/services/indexing.py), so on every
-- installation since, the default has meant "index with the code that no longer
-- exists": `crawling.claim` returns nothing, the edge crawler polls an empty queue,
-- and a freshly added channel sits in `indexing` forever with no error anywhere.
--
-- Only the rows still holding the dead default are touched; an operator who chose
-- something else keeps their choice.
UPDATE feature_flags
   SET value = '"crawler"',
       description = 'Where the catalogue comes from: "crawler" (web preview, ADR-002)',
       updated_at = now()
 WHERE key = 'indexing_source' AND value = '"mtproto"';

-- Never read by any code: lazy resolution is unconditional since ADR-003, decided by
-- the track's own resolve_status. A switch that switches nothing is worse than none.
DELETE FROM feature_flags WHERE key = 'lazy_resolve';
