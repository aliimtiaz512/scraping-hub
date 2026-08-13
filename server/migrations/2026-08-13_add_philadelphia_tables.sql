-- Creates the City of Philadelphia (PHLContracts) tables — see
-- app/scrapers/philadelphia/models.py. On a fresh database create_all makes
-- these automatically and this file is not needed; run it once against an
-- existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-13_add_philadelphia_tables.sql
--
-- Idempotent — safe to run more than once.

CREATE TABLE IF NOT EXISTS philadelphia_runs (
    run_id               VARCHAR(32) PRIMARY KEY,
    status               VARCHAR(32),
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    search               TEXT,
    bids_found           INTEGER DEFAULT 0,
    documents_downloaded INTEGER DEFAULT 0,
    folder               TEXT,
    excel_path           TEXT,
    ingested_at          TIMESTAMPTZ DEFAULT now()
);

-- One row per BID, not per (run, bid): the portal's Open Bids list is a live
-- set and the same bid appears in it every day until it closes, so a second run
-- updates the row rather than adding a copy. `first_seen_at` records when it
-- first appeared; `scraped_at` moves with each sighting.
--
-- run_id is ON DELETE SET NULL: clearing an old run's history must not delete
-- bids that are still open.
CREATE TABLE IF NOT EXISTS city_of_philadelphia_bids (
    bid_number           VARCHAR(64) PRIMARY KEY,
    run_id               VARCHAR(32) REFERENCES philadelphia_runs(run_id) ON DELETE SET NULL,

    organization         TEXT,
    alternate_id         TEXT,
    buyer                TEXT,
    description          TEXT,
    bid_opening_date     VARCHAR(64),

    detail_url           TEXT,
    -- Every label/value pair under the detail page's "Header Information", as
    -- published. JSONB rather than columns: the labels differ per bid type, so
    -- pinning them down would mean a migration each time the city adds a row.
    extra_header_data    JSONB DEFAULT '{}'::jsonb,
    -- Saved attachments as `Bids_Data/<bid>/<file>` paths inside the archive.
    file_paths           JSONB DEFAULT '[]'::jsonb,
    documents_downloaded INTEGER DEFAULT 0,
    document_errors      JSONB DEFAULT '[]'::jsonb,

    first_seen_at        TIMESTAMPTZ DEFAULT now(),
    scraped_at           TIMESTAMPTZ DEFAULT now(),
    raw_data             JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_city_of_philadelphia_bids_run_id
    ON city_of_philadelphia_bids (run_id);
CREATE INDEX IF NOT EXISTS ix_city_of_philadelphia_bids_scraped_at
    ON city_of_philadelphia_bids (scraped_at DESC);
