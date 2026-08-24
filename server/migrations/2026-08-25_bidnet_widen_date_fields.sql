-- Widens bidnet_bids' three date-ish columns from VARCHAR(64) to TEXT.
--
-- They hold whatever the issuing agency typed into a date field, and across the
-- five hundred-odd member agencies a BidNet sweep covers, that is regularly not
-- a date: "See specification for the submission schedule", a date plus timezone
-- plus parenthetical, a pointer to an addendum.
--
-- At 64 characters one such value raises StringDataRightTruncation. Postgres
-- does not truncate, it aborts — and because a run saves its bids in a single
-- transaction, one oversized cell rolled back all 1,859 records of a completed
-- sweep. The scraper now also trims oversized values before sending them
-- (export._fit), so this is the second of two independent guards.
--
-- Nothing reads these as dates in the database; the close-date rule parses them
-- in Python. The limit protected nothing.
--
-- On a fresh database create_all makes these as TEXT and this file is not
-- needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-25_bidnet_widen_date_fields.sql
--
-- Idempotent - safe to run more than once. Widening to TEXT in Postgres is a
-- catalog-only change: no table rewrite, no index rebuild, no downtime.

ALTER TABLE bidnet_bids ALTER COLUMN publication_date TYPE TEXT;
ALTER TABLE bidnet_bids ALTER COLUMN question_acceptance_deadline TYPE TEXT;
ALTER TABLE bidnet_bids ALTER COLUMN closing_date TYPE TEXT;
