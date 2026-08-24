-- Widens bidnet_bids.niche from VARCHAR(64) to VARCHAR(255).
--
-- The column holds the niche label for a niche run and the **issuing agency**
-- for a "Run all member agency bids" sweep (see
-- app/scrapers/bidnet/member_agencies.py). Agencies name themselves at length —
-- "City and County of Denver Climate Action, Sustainability & Resiliency" is 68
-- characters — and one value over the limit fails the run's entire insert with
-- `StringDataRightTruncation`, not just its own row.
--
-- On a fresh database create_all makes the column at the new width and this
-- file is not needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-24_bidnet_widen_niche.sql
--
-- Idempotent - safe to run more than once. Widening a varchar in Postgres is a
-- catalog-only change: no table rewrite, no index rebuild, no downtime.

ALTER TABLE bidnet_bids ALTER COLUMN niche TYPE VARCHAR(255);
