-- A Buy # with no underscore has no repost suffix, which used to leave
-- bid_upload_count NULL and the export's "Bid Upload Count" cell blank. It now
-- reads "0" — a count of none, rather than an empty cell a reader has to
-- interpret. This backfills the rows scraped before that.
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-07_unison_bid_upload_count_zero.sql
--
-- Idempotent — safe to run more than once. Only touches rows that were parsed
-- from a Buy # (a row with no buyer_number never had a suffix to find).

UPDATE unison_requests
   SET bid_upload_count = '0'
 WHERE bid_upload_count IS NULL
   AND buyer_number IS NOT NULL;

ALTER TABLE unison_requests ALTER COLUMN bid_upload_count SET DEFAULT '0';
