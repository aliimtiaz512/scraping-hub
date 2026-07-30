-- Adds the MyFlorida ad-status sweep tables (see
-- app/scrapers/myflorida/sweep/models.py). The sweep classifies every
-- advertisement matching an ad status into one of six niches or Other, per
-- MFMP_Niche_Classification_Criteria.md.
--
-- Two tables rather than one: mfmp_sweep_scores keeps all six niche scores for
-- every advertisement, including the losers, because the criteria doc's tuning
-- signal (§5.2) is "a cluster of 35s all pointing at one niche means a lexicon
-- gap" — which only exists if the near-misses were kept. It also lets a
-- threshold change be replayed over history without re-scraping.
--
-- On a fresh database create_all makes these automatically and this file is not
-- needed; run it once against an existing database:
--
--   psql "$DATABASE_URL" -f server/migrations/2026-07-30_add_mfmp_sweep_tables.sql
--
-- Idempotent — safe to run more than once.

CREATE TABLE IF NOT EXISTS mfmp_sweep_bids (
    id                    BIGSERIAL PRIMARY KEY,
    run_id                VARCHAR(32)  NOT NULL,
    scraped_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

    ad_number             VARCHAR(120) NOT NULL,
    title                 TEXT,
    agency                TEXT,
    ad_type               VARCHAR(120),
    status                VARCHAR(60),
    ad_date               VARCHAR(60),
    open_date             VARCHAR(60),
    close_date            VARCHAR(60),
    description           TEXT,

    primary_niche         VARCHAR(16)  NOT NULL,
    primary_score         INTEGER      DEFAULT 0,
    match_strength        VARCHAR(16),
    secondary_niches      JSONB,
    other_reason          VARCHAR(40),
    closest_niche         VARCHAR(16),
    closest_niche_score   INTEGER,
    flags                 JSONB,

    matched_codes         JSONB,
    code_source           VARCHAR(20),
    matched_keywords      JSONB,
    suppressed_terms      JSONB,
    deliverables_detected JSONB,

    documents             JSONB,
    document_chars        INTEGER      DEFAULT 0,
    raw_data              JSONB
);

CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_bids_run_id       ON mfmp_sweep_bids (run_id);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_bids_ad_number    ON mfmp_sweep_bids (ad_number);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_bids_primary_niche ON mfmp_sweep_bids (primary_niche);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_bids_run_niche    ON mfmp_sweep_bids (run_id, primary_niche);

CREATE TABLE IF NOT EXISTS mfmp_sweep_scores (
    id               BIGSERIAL PRIMARY KEY,
    bid_id           BIGINT       NOT NULL REFERENCES mfmp_sweep_bids (id) ON DELETE CASCADE,
    run_id           VARCHAR(32)  NOT NULL,
    ad_number        VARCHAR(120) NOT NULL,

    niche            VARCHAR(16)  NOT NULL,
    total            INTEGER      DEFAULT 0,
    code_points      INTEGER      DEFAULT 0,
    title_points     INTEGER      DEFAULT 0,
    scope_points     INTEGER      DEFAULT 0,
    matched_keywords JSONB,
    suppressed_terms JSONB
);

CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_scores_bid_id    ON mfmp_sweep_scores (bid_id);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_scores_run_id    ON mfmp_sweep_scores (run_id);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_scores_ad_number ON mfmp_sweep_scores (ad_number);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_scores_niche     ON mfmp_sweep_scores (niche);
CREATE INDEX IF NOT EXISTS ix_mfmp_sweep_scores_run_niche ON mfmp_sweep_scores (run_id, niche);
