-- Adds the record-completeness columns to bidnet_bids (see
-- app/scrapers/bidnet/models.py and scraper.RECORD_STATUSES). On a fresh
-- database create_all makes these automatically and this file is not needed;
-- run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-03_add_bidnet_bid_status.sql
--
-- Idempotent - safe to run more than once.

-- OK | PARTIAL_DATA | EXTRACTION_FAILED. Bids scraped before this column
-- existed are backfilled by inspecting what was actually captured.
ALTER TABLE bidnet_bids ADD COLUMN IF NOT EXISTS status VARCHAR(32);
ALTER TABLE bidnet_bids ADD COLUMN IF NOT EXISTS detail_url TEXT;

CREATE INDEX IF NOT EXISTS ix_bidnet_bids_status ON bidnet_bids (status);

UPDATE bidnet_bids
   SET status = CASE
       WHEN COALESCE(reference_number, '') = '' AND COALESCE(title, '') = ''
            AND COALESCE(closing_date, '') = '' THEN 'EXTRACTION_FAILED'
       WHEN COALESCE(reference_number, '') = '' OR COALESCE(title, '') = ''
            THEN 'PARTIAL_DATA'
       ELSE 'OK'
   END
 WHERE status IS NULL;
