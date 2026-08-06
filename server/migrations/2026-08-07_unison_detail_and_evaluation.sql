-- Unison now opens each buy's detail page: it stores the General Buy
-- Information fields, the Shipping Information (place of performance), the line
-- items, the attachments it downloaded, and the evaluator's verdict — the same
-- funnel SAM runs, per Company_Bid_Selection_Criteria.docx.
--
-- On a fresh database create_all makes all of this and the file is not needed;
-- run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-07_unison_detail_and_evaluation.sql
--
-- Idempotent — safe to run more than once. Existing rows keep NULLs: they were
-- scraped from the dashboard only, and no detail page was ever opened for them.

-- Buy # parsing -------------------------------------------------------------
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS bid_upload_count VARCHAR(16);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS detail_url       TEXT;

-- General Buy Information ---------------------------------------------------
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS solicitation_number      VARCHAR(255);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS category                 TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS subcategory              TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS naics                    TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS naics_size_standard      TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS sam_contract_opportunity VARCHAR(64);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS set_aside                TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS end_time                 VARCHAR(64);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS seller_question_deadline TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS delivery                 TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS repost_reason            TEXT;

-- Shipping Information ------------------------------------------------------
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS shipping_city  TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS shipping_state VARCHAR(128);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS shipping_zip   VARCHAR(32);

-- Line items and attachments ------------------------------------------------
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS line_item_count             INTEGER DEFAULT 0;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS line_items                  JSONB DEFAULT '[]'::jsonb;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS seller_attachments_required TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS attachment_count            INTEGER DEFAULT 0;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS attachments                 JSONB DEFAULT '[]'::jsonb;

-- Evaluation ----------------------------------------------------------------
-- decision/reason are exported; the rest exist to audit a decision later.
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS decision           VARCHAR(32);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS reason             TEXT;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS requirement_type   VARCHAR(16);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS rule               VARCHAR(16);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS location           VARCHAR(32);
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS requirement_hinted BOOLEAN DEFAULT FALSE;
ALTER TABLE unison_requests ADD COLUMN IF NOT EXISTS detail_sections    JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_unison_requests_decision ON unison_requests (decision);

-- Run-level: which portal filter the run used, and how many pages it walked --
ALTER TABLE unison_runs ADD COLUMN IF NOT EXISTS filter_id     VARCHAR(8);
ALTER TABLE unison_runs ADD COLUMN IF NOT EXISTS filter_label  TEXT;
ALTER TABLE unison_runs ADD COLUMN IF NOT EXISTS pages_scraped INTEGER DEFAULT 0;
