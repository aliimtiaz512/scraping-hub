-- A RideMetro run now signs in as one of two configured accounts (Hoope Lab or
-- Fedpints — see app/scrapers/ridemetro/accounts.py), and records which. The two
-- accounts belong to different Euna Supplier Networks, so without this column
-- two runs' agency lists are not comparable: a network that shrank and a run
-- under the other account look identical.
--
-- On a fresh database create_all makes this and the file is not needed; run it
-- once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-06_ridemetro_account.sql
--
-- Idempotent — safe to run more than once.

ALTER TABLE ridemetro_runs ADD COLUMN IF NOT EXISTS account VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_ridemetro_runs_account ON ridemetro_runs (account);

-- Every run that predates the account switch used the credentials that are now
-- the Hoope Lab account's, so name them rather than leaving them unattributed.
UPDATE ridemetro_runs SET account = 'hoope_lab' WHERE account IS NULL;
