-- Migration: 2026-10-14_ppra_tables.sql
-- Description: Tables for PPRA federal tenders (Pakistan region)

CREATE TABLE IF NOT EXISTS ppra_runs (
  run_id          text PRIMARY KEY,
  parent_run_id   text REFERENCES ppra_runs(run_id) ON DELETE SET NULL,
  status          text NOT NULL,
  filters         jsonb NOT NULL,
  filters_hash    text  NOT NULL,
  options         jsonb NOT NULL DEFAULT '{}',
  total_expected  integer,
  total_pages     integer,
  current_page    integer NOT NULL DEFAULT 0,
  rows_scraped    integer NOT NULL DEFAULT 0,
  rows_new        integer NOT NULL DEFAULT 0,
  rows_changed    integer NOT NULL DEFAULT 0,
  skipped_closing integer NOT NULL DEFAULT 0,
  kept_unverified integer NOT NULL DEFAULT 0,
  checkpoint      jsonb NOT NULL DEFAULT '{}',
  error           text,
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ppra_runs_created ON ppra_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ppra_runs_hash    ON ppra_runs (filters_hash);

CREATE TABLE IF NOT EXISTS ppra_bids (
  id              bigserial PRIMARY KEY,
  run_id          text NOT NULL REFERENCES ppra_runs(run_id) ON DELETE CASCADE,
  page_no         integer NOT NULL,
  position        integer NOT NULL,
  tender_no       text NOT NULL,
  title           text NOT NULL,
  description     text,
  sector          text,
  tender_ref_no   text,
  tender_type     text,
  category        text,
  nature          text,
  organization    text,
  department      text,
  city            text,
  country         text,
  status          text,
  advertised_on   date,
  closing_at      timestamptz,
  detail_url      text,
  invoice_url     text,
  content_hash    text NOT NULL,
  change_status   text NOT NULL DEFAULT 'new',
  diff            jsonb,
  raw_data        jsonb NOT NULL,
  scraped_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_ppra_run_tender UNIQUE (run_id, tender_no)
);

CREATE INDEX IF NOT EXISTS ix_ppra_bids_run_id   ON ppra_bids (run_id, id);
CREATE INDEX IF NOT EXISTS ix_ppra_bids_closing  ON ppra_bids (closing_at);
CREATE INDEX IF NOT EXISTS ix_ppra_bids_tender   ON ppra_bids (tender_no);
CREATE INDEX IF NOT EXISTS ix_ppra_bids_org      ON ppra_bids (organization);

CREATE TABLE IF NOT EXISTS ppra_filter_options (
  kind          text NOT NULL,
  value         text NOT NULL,
  label         text NOT NULL,
  sort_order    integer NOT NULL DEFAULT 0,
  refreshed_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, value)
);

