-- RideMetro now sweeps the whole Euna Supplier Network instead of the single
-- RideMetro portal (see app/scrapers/ridemetro/). That adds the agency a bid
-- came from, the run's agency roster, and widens the per-run uniqueness key:
-- two agencies can issue the same reference number, so (run_id, ref_number)
-- would collapse them into one row.
--
-- On a fresh database create_all makes all of this and this file is not needed;
-- run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-06_ridemetro_supplier_network.sql
--
-- Idempotent — safe to run more than once.

ALTER TABLE ridemetro_bids ADD COLUMN IF NOT EXISTS agency     TEXT;
ALTER TABLE ridemetro_bids ADD COLUMN IF NOT EXISTS agency_url TEXT;

CREATE INDEX IF NOT EXISTS ix_ridemetro_bids_agency ON ridemetro_bids (agency);

-- Pre-sweep rows all came from the RideMetro portal itself; naming them keeps
-- the new uniqueness key meaningful for historical runs (NULL agency would make
-- every old row unique regardless of its reference).
UPDATE ridemetro_bids
   SET agency = 'Metropolitan Transit Authority of Harris County (METRO)',
       agency_url = 'https://ridemetro.bonfirehub.com'
 WHERE agency IS NULL;

ALTER TABLE ridemetro_bids DROP CONSTRAINT IF EXISTS uq_ridemetro_run_ref;
DO $$
BEGIN
    ALTER TABLE ridemetro_bids
        ADD CONSTRAINT uq_ridemetro_run_agency_ref UNIQUE (run_id, agency, ref_number);
EXCEPTION
    WHEN duplicate_table THEN NULL;   -- constraint already present
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE ridemetro_runs ADD COLUMN IF NOT EXISTS agencies_found   INTEGER DEFAULT 0;
ALTER TABLE ridemetro_runs ADD COLUMN IF NOT EXISTS agencies_scraped INTEGER DEFAULT 0;
ALTER TABLE ridemetro_runs ADD COLUMN IF NOT EXISTS agencies         JSONB DEFAULT '[]'::jsonb;
