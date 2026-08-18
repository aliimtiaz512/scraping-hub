-- Philadelphia: the shared evaluation matrix's verdict, stored per bid — see
-- app/scrapers/philadelphia/evaluation.py. Same Rule A/B/C funnel SAM and
-- Unison use, so `decision` here means what `decision` means on sam_bids.
--
-- Stored rather than re-derived when a sheet is rebuilt: the kill-word list the
-- funnel reads is editable from the console, so re-running the matrix months
-- later would silently rewrite a verdict somebody already acted on.
--
-- The verdict is a column, never a filter. Every bid the city published stays
-- in the report with its folder and its documents; the matrix only says what
-- the run thinks of it.
--
-- On a fresh database create_all makes these automatically and this file is not
-- needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-17_philadelphia_evaluation_matrix.sql
--
-- Idempotent — safe to run more than once. Existing rows get NULL and are
-- filled in by the next run that sees the bid.

ALTER TABLE city_of_philadelphia_bids
    ADD COLUMN IF NOT EXISTS decision         VARCHAR(20),
    ADD COLUMN IF NOT EXISTS reason           TEXT,
    ADD COLUMN IF NOT EXISTS rule             VARCHAR(16),
    ADD COLUMN IF NOT EXISTS requirement_type VARCHAR(20);
