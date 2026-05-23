-- ============================================================
-- domytrade.app — Initial Schema
-- Feature 1: Hourly VB Models (Conservative & Aggressive)
-- ============================================================


-- ── Symbols ──────────────────────────────────────────────────
-- Master list of tradeable symbols tracked by the platform.
CREATE TABLE symbols (
    id            SERIAL PRIMARY KEY,
    ticker        TEXT UNIQUE NOT NULL,        -- display ticker: 'SPY', '/ES'
    schwab_symbol TEXT NOT NULL,               -- Schwab API symbol: 'SPY', '/ES:XCME'
    asset_type    TEXT NOT NULL                -- 'stock' | 'future'
                  CHECK (asset_type IN ('stock', 'future')),
    is_active     BOOLEAN DEFAULT true,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Seed initial 20 symbols
INSERT INTO symbols (ticker, schwab_symbol, asset_type) VALUES
    -- Stocks
    ('SPY',  'SPY',       'stock'),
    ('QQQ',  'QQQ',       'stock'),
    ('AAPL', 'AAPL',      'stock'),
    ('BABA', 'BABA',      'stock'),
    -- Equity Index Futures
    ('/ES',  '/ES:XCME',  'future'),
    ('/NQ',  '/NQ:XCME',  'future'),
    ('/RTY', '/RTY:XCME', 'future'),
    ('/YM',  '/YM:XCBT',  'future'),
    -- Energy Futures
    ('/CL',  '/CL:XNYM',  'future'),
    ('/NG',  '/NG:XNYM',  'future'),
    -- Metals Futures
    ('/GC',  '/GC:XCEC',  'future'),
    ('/SI',  '/SI:XCEC',  'future'),
    ('/HG',  '/HG:XCEC',  'future'),
    -- Bond Futures
    ('/ZB',  '/ZB:XCBT',  'future'),
    ('/ZN',  '/ZN:XCBT',  'future'),
    -- Agriculture Futures
    ('/ZC',  '/ZC:XCBT',  'future'),
    ('/ZS',  '/ZS:XCBT',  'future'),
    -- Other
    ('/PL',  '/PL:XNYM',  'future'),
    ('/RB',  '/RB:XNYM',  'future'),
    ('/BTC', '/BTC:XCME', 'future');


-- ── OHLC Hourly ──────────────────────────────────────────────
-- 6 months of hourly bars. Foundation for all stat computation.
-- ~4,300 bars × 20 symbols = ~86,000 rows.
CREATE TABLE ohlc_hourly (
    id         BIGSERIAL PRIMARY KEY,
    symbol_id  INT NOT NULL REFERENCES symbols(id),
    bar_time   TIMESTAMPTZ NOT NULL,    -- UTC start of the hour
    hour_et    SMALLINT NOT NULL        -- ET hour 0–23
               CHECK (hour_et BETWEEN 0 AND 23),
    open       NUMERIC(14, 4) NOT NULL,
    high       NUMERIC(14, 4) NOT NULL,
    low        NUMERIC(14, 4) NOT NULL,
    close      NUMERIC(14, 4) NOT NULL,
    volume     BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (symbol_id, bar_time)
);

CREATE INDEX idx_ohlc_symbol_time ON ohlc_hourly (symbol_id, bar_time DESC);
CREATE INDEX idx_ohlc_symbol_hour ON ohlc_hourly (symbol_id, hour_et);


-- ── VBH Stats ────────────────────────────────────────────────
-- Precomputed L1/L2/L3/L4 per symbol, model, and ET hour.
-- Formula: L1=mean-σ, L2=mean, L3=mean+σ, L4=25th-pct (unclamped).
-- Refreshed once daily. 24 × 2 × 20 = 960 rows.
CREATE TABLE vbh_stats (
    id            SERIAL PRIMARY KEY,
    symbol_id     INT NOT NULL REFERENCES symbols(id),
    model         TEXT NOT NULL CHECK (model IN ('AGG', 'CON')),
    hour_et       SMALLINT NOT NULL CHECK (hour_et BETWEEN 0 AND 23),
    l1            NUMERIC(14, 5),       -- mean - σ  (lower cyan)
    l2            NUMERIC(14, 5),       -- mean      (mid green)
    l3            NUMERIC(14, 5),       -- mean + σ  (upper red)
    l4            NUMERIC(14, 5),       -- 25th pct  (target gray)
    sample_count  SMALLINT,
    lookback_days SMALLINT,            -- 30 for AGG, 90 for CON
    computed_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (symbol_id, model, hour_et)
);

CREATE INDEX idx_vbh_stats_lookup ON vbh_stats (symbol_id, model, hour_et);


-- ── VBH Signals ──────────────────────────────────────────────
-- One row per signal per hourly refresh. Stored for future
-- backtesting and win-rate analysis. Low volume: ~40 rows/hour.
CREATE TABLE vbh_signals (
    id             BIGSERIAL PRIMARY KEY,
    symbol_id      INT NOT NULL REFERENCES symbols(id),
    model          TEXT NOT NULL CHECK (model IN ('AGG', 'CON')),
    side           TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    entry          NUMERIC(14, 4),
    stop           NUMERIC(14, 4),
    target         NUMERIC(14, 4),
    last_price     NUMERIC(14, 4),
    hour_high      NUMERIC(14, 4),
    hour_low       NUMERIC(14, 4),
    current_range  NUMERIC(14, 4),
    typical_range  NUMERIC(14, 4),
    swing_pct      NUMERIC(6, 1),
    signal_hour    TIMESTAMPTZ NOT NULL,    -- which market hour this fires for
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_vbh_signals_hour   ON vbh_signals (signal_hour DESC);
CREATE INDEX idx_vbh_signals_symbol ON vbh_signals (symbol_id, signal_hour DESC);


-- ── Profiles ─────────────────────────────────────────────────
-- Extends Supabase Auth. One row per registered user.
CREATE TABLE profiles (
    id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email      TEXT,
    plan       TEXT DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO profiles (id, email)
    VALUES (NEW.id, NEW.email);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_user();


-- ── Row Level Security ────────────────────────────────────────
ALTER TABLE symbols     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ohlc_hourly ENABLE ROW LEVEL SECURITY;
ALTER TABLE vbh_stats   ENABLE ROW LEVEL SECURITY;
ALTER TABLE vbh_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles    ENABLE ROW LEVEL SECURITY;

-- Public read on market data (auth required to reach the app anyway)
CREATE POLICY "authenticated read symbols"     ON symbols     FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated read ohlc"        ON ohlc_hourly FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated read vbh_stats"   ON vbh_stats   FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated read vbh_signals" ON vbh_signals FOR SELECT TO authenticated USING (true);

-- Backend service role writes everything (uses service_role key, bypasses RLS)
-- No explicit policy needed — service_role bypasses RLS by default.

-- Users can only read/update their own profile
CREATE POLICY "own profile read"   ON profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "own profile update" ON profiles FOR UPDATE USING (auth.uid() = id);
