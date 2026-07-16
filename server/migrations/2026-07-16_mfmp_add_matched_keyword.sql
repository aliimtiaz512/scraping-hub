-- Adds Bid.matched_keyword (see app/scrapers/myflorida/models.py).
-- create_tables.py uses create_all, which will NOT alter an existing table, so
-- run this once against any database created before this change:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-07-16_mfmp_add_matched_keyword.sql
--
-- Idempotent — safe to run more than once. Fresh databases get the column from
-- create_all and don't need this.
ALTER TABLE mfmp_bids ADD COLUMN IF NOT EXISTS matched_keyword TEXT;
