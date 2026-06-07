-- ============================================================
-- GEX Phase 2: Daily snapshots for tracked stock symbols
-- + OCC market-maker % table for future Synthetic OI
-- ============================================================

-- ── Table 1: Tracked stock symbols ───────────────────────────
-- Controls which non-index symbols get a nightly GEX baseline.
-- Index symbols (SPX/NDX/RUT) are hardcoded in GEX_INDEX_SYMBOLS.
CREATE TABLE IF NOT EXISTS gex_tracked_symbols (
    symbol      TEXT PRIMARY KEY,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed initial watchlist
INSERT INTO gex_tracked_symbols (symbol) VALUES
    ('SPY'),
    ('QQQ'),
    ('MSFT'),
    ('NVDA'),
    ('MU')
ON CONFLICT (symbol) DO NOTHING;

-- ── Table 2: OCC market-maker % per expiry per date ───────────
-- Source: OCC volume-query API (per-expiry account-type breakdown).
-- mm_pct_calls / mm_pct_puts are 0..1 fractions of volume from MMs.
-- Used later to compute Synthetic OI = total_OI × mm_pct.
CREATE TABLE IF NOT EXISTS gex_mm_pct (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    snapshot_date   DATE NOT NULL,
    expiry          DATE NOT NULL,
    mm_pct_calls    NUMERIC(6, 4),   -- e.g. 0.3812
    mm_pct_puts     NUMERIC(6, 4),
    total_call_vol  INTEGER,
    total_put_vol   INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT gex_mm_pct_uniq UNIQUE (symbol, snapshot_date, expiry)
);

CREATE INDEX IF NOT EXISTS gex_mm_pct_sym_date_idx
    ON gex_mm_pct (symbol, snapshot_date DESC);
