-- Adds raw_data JSONB to bidnet_bids and wisconsin_bids (see the respective
-- app/scrapers/<portal>/models.py). The batched save now preserves the full
-- scraped record in raw_data, mirroring mfmp_bids / ridemetro_bids.
--
-- create_tables.py uses create_all, which will NOT alter an existing table, so
-- run this once against any database created before this change:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-07-18_add_raw_data_bidnet_wisconsin.sql
--
-- Idempotent — safe to run more than once. Fresh databases get the column from
-- create_all and don't need this.
ALTER TABLE bidnet_bids ADD COLUMN IF NOT EXISTS raw_data JSONB DEFAULT '{}'::jsonb;
ALTER TABLE wisconsin_bids ADD COLUMN IF NOT EXISTS raw_data JSONB DEFAULT '{}'::jsonb;
