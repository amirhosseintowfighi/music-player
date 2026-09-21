-- 0007 — the cover's dominant colours (ADR-003 phase 12).
--
-- The player paints a gradient from the artwork. Computing it needs the pixels, and
-- the pixels only exist on the client (the core never sees a byte of media), so the
-- first listener who renders a track reports what it found and everyone after that
-- gets the colours with the metadata — before the image has even loaded.
ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS cover_palette text;
