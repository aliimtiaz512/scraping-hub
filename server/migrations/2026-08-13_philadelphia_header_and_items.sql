-- Philadelphia: the detail page's header table and line items become
-- deliverables a client can open — see app/scrapers/philadelphia/details.py.
--
-- What changed: `Extra_Header_Info.json` is no longer written beside each bid.
-- Three of its fields are promoted to columns here (and to columns in the
-- summary sheet), the rest reach the sheet as one readable cell rendered from
-- `extra_header_data`, which is unchanged and still holds every published
-- label. The bid's line items are captured for the first time and stored in
-- `items`, so `bid_items_details.txt` can be rebuilt from the database rather
-- than only from a workspace that is deleted when the run ends.
--
-- On a fresh database create_all makes these automatically and this file is not
-- needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-13_philadelphia_header_and_items.sql
--
-- Idempotent — safe to run more than once. Existing rows get NULL/'[]' and are
-- filled in by the next run that sees the bid.

ALTER TABLE city_of_philadelphia_bids
    ADD COLUMN IF NOT EXISTS fiscal_year        VARCHAR(64),
    ADD COLUMN IF NOT EXISTS solicitation_type  TEXT,
    ADD COLUMN IF NOT EXISTS pre_bid_conference TEXT,
    ADD COLUMN IF NOT EXISTS items              JSONB DEFAULT '[]'::jsonb;
