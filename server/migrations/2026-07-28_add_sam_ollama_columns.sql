-- Adds the Ollama-wall audit columns to sam_bids (see
-- app/scrapers/sam/models.py :: SamBid). These store the raw output of the
-- Ollama evaluation wall for every MANUAL_REVIEW bid it is consulted on, so its
-- accuracy can be audited over time independently of which decision was
-- accepted. NULL for bids that never reached the wall (PURSUE/REJECT from the
-- deterministic rules). On a fresh database create_all makes these columns
-- automatically and this file is not needed; run it once against an existing
-- database that predates the Ollama integration:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-07-28_add_sam_ollama_columns.sql
--
-- Idempotent — safe to run more than once.

ALTER TABLE sam_bids ADD COLUMN IF NOT EXISTS ollama_decision   VARCHAR(20);
ALTER TABLE sam_bids ADD COLUMN IF NOT EXISTS ollama_rule       VARCHAR(120);
ALTER TABLE sam_bids ADD COLUMN IF NOT EXISTS ollama_confidence VARCHAR(10);
