-- ============================================================
-- domytrade.app — Migration 002
-- Add ohlc_30min table — 30-minute OHLCV bars
--
-- Mirrors ohlc_hourly structure but at 30-min granularity.
-- VBH stats are computed from this table instead of ohlc_hourly
-- so the calculation matches the original TOS study (30-min TF).
-- ============================================================

CREATE TABLE IF NOT EXISTS ohlc_30min (
    id         BIGSERIAL PRIMARY KEY,
    symbol_id  INT NOT NULL REFERENCES symbols(id),
    bar_time   TIMESTAMPTZ NOT NULL,        -- UTC start of the 30-min bar
    hour_et    SMALLINT NOT NULL            -- ET hour 0–23
               CHECK (hour_et BETWEEN 0 AND 23),
    minute_et  SMALLINT NOT NULL            -- 0 or 30
               CHECK (minute_et IN (0, 30)),
    open       NUMERIC(14, 4) NOT NULL,
    high       NUMERIC(14, 4) NOT NULL,
    low        NUMERIC(14, 4) NOT NULL,
    close      NUMERIC(14, 4) NOT NULL,
    volume     BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (symbol_id, bar_time)
);

-- Index for fast lookback queries by symbol + time range
CREATE INDEX IF NOT EXISTS ohlc_30min_symbol_time
    ON ohlc_30min (symbol_id, bar_time DESC);

-- Index for per-hour analysis (hour_et queries)
CREATE INDEX IF NOT EXISTS ohlc_30min_symbol_hour
    ON ohlc_30min (symbol_id, hour_et);

-- RLS — same pattern as ohlc_hourly
ALTER TABLE ohlc_30min ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated read ohlc_30min"
    ON ohlc_30min FOR SELECT TO authenticated USING (true);

-- Service role bypasses RLS for writes automatically.
