-- Adds the term-kind column to the BidNet niche catalog (see
-- app/scrapers/bidnet/niche_models.py). A niche now owns two lists that are
-- searched through the same box, one term at a time: its keywords, then its
-- NIGP class-item / UNSPSC codes. Both live in bidnet_niche_keywords and are
-- told apart by `kind`.
--
-- On a fresh database create_all makes the column automatically and this file
-- is not needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-08-11_add_bidnet_niche_kind.sql
--
-- The rows themselves are NOT inserted here — the catalog is re-seeded from
-- app/scrapers/bidnet/niches.py every time the API starts, which is what fills
-- in each niche's codes.
--
-- Idempotent — safe to run more than once.

-- 'keyword' | 'nigp'. Every row that predates the codes is a keyword, which is
-- exactly what the default backfills them to.
ALTER TABLE bidnet_niche_keywords
    ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'keyword';

-- The catalog is read one niche at a time, ordered by sort_order; the kind is
-- read alongside rather than filtered on, so no extra index is warranted.
