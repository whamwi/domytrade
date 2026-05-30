-- ============================================================
-- domytrade.app — Migration 003
-- Asset Personality Profile
--
-- Stores per-hour backtest results for each active futures
-- symbol across all three VBH models (AGG, CON, WIDE).
-- Computed from 365 days of 30-min bar data.
-- Refreshed weekly alongside the VBH stats update.
-- ============================================================

CREATE TABLE IF NOT EXISTS asset_personality (
    id              BIGSERIAL PRIMARY KEY,
    symbol_id       INT NOT NULL REFERENCES symbols(id),
    model           VARCHAR(10) NOT NULL        -- AGG | CON | WIDE
                    CHECK (model IN ('AGG','CON','WIDE')),
    hour_et         SMALLINT NOT NULL           -- 0–23 ET
                    CHECK (hour_et BETWEEN 0 AND 23),
    session         VARCHAR(10) NOT NULL        -- RTH | OFF
                    CHECK (session IN ('RTH','OFF')),

    -- ── Backtest aggregate stats ──────────────────────────────
    total_trades    INT,
    wins            INT,
    losses          INT,
    win_rate        NUMERIC(5,2),               -- %
    net_pnl_usd     NUMERIC(14,2),              -- $ per contract
    avg_pnl_usd     NUMERIC(10,2),              -- $ avg per trade

    -- ── Directional breakdown ─────────────────────────────────
    long_trades     INT,
    long_wins       INT,
    long_win_rate   NUMERIC(5,2),
    long_net_usd    NUMERIC(14,2),
    short_trades    INT,
    short_wins      INT,
    short_win_rate  NUMERIC(5,2),
    short_net_usd   NUMERIC(14,2),

    -- ── Contract reference ────────────────────────────────────
    lot_value_usd   NUMERIC(10,2),              -- $/point multiplier
    buf_pts         NUMERIC(10,4),              -- stop buffer in price points

    -- ── Derived personality tags ──────────────────────────────
    direction_bias  VARCHAR(10)                 -- LONG | SHORT | NEUTRAL | AVOID
                    CHECK (direction_bias IN ('LONG','SHORT','NEUTRAL','AVOID')),
    signal_strength VARCHAR(10)                 -- STRONG | MODERATE | WEAK | DEAD
                    CHECK (signal_strength IN ('STRONG','MODERATE','WEAK','DEAD')),

    -- ── Meta ──────────────────────────────────────────────────
    lookback_days   INT DEFAULT 365,
    computed_at     TIMESTAMPTZ DEFAULT now(),

    UNIQUE (symbol_id, model, hour_et)
);

-- Fast reads by symbol + model
CREATE INDEX IF NOT EXISTS asset_personality_sym_model
    ON asset_personality (symbol_id, model);

-- Fast reads by session
CREATE INDEX IF NOT EXISTS asset_personality_session
    ON asset_personality (session, model);

-- RLS
ALTER TABLE asset_personality ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated read asset_personality"
    ON asset_personality FOR SELECT TO authenticated USING (true);

-- Service role bypasses RLS for writes automatically.
