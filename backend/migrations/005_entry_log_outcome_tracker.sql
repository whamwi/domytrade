-- ─────────────────────────────────────────────────────────────────────────────
-- 005 — Entry Log Outcome Tracker + Archive
--
-- Rebuilds entry_log with outcome-tracking columns (clean slate — existing
-- rows discarded, new concept).
-- Adds entry_log_archive: receives rows moved out of entry_log every 2 days
-- by the archive job, and also receives all rows when "Clear Log" is clicked
-- so no data is ever truly lost.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Drop & recreate entry_log ────────────────────────────────────────────────
drop table if exists entry_log cascade;

create table entry_log (
    id              bigserial       primary key,

    -- ── Signal identity ──────────────────────────────────────────────────────
    fired_at        timestamptz     not null default now(),
    symbol          text            not null,
    model           text            not null,   -- 'AGG' | 'CON' | 'WIDE' | 'CR'
    side            text            not null,   -- 'LONG' | 'SHORT'
    hour_et         integer,                    -- 0-23 ET hour when signal fired
    daily_bias      text,                       -- 'LONG' | 'SHORT' | null

    -- ── Box levels at fire time ───────────────────────────────────────────────
    entry           numeric,
    stop            numeric,
    t1              numeric,
    target          numeric,
    last_price      numeric,                    -- price at moment of ENTRY alert

    -- ── Outcome ──────────────────────────────────────────────────────────────
    outcome         text            default 'OPEN',
                                                -- 'OPEN' | 'HIT_TARGET' | 'HIT_STOP' | 'EXPIRED'
    outcome_at      timestamptz,                -- when outcome was determined
    pnl_pts         numeric,                    -- points: positive = winner, negative = loser

    -- ── Price snapshots (auto-stamped by background loop) ────────────────────
    price_1h        numeric,                    -- price ~1h after fired_at
    price_4h        numeric,                    -- price ~4h after fired_at
    price_eod       numeric,                    -- price at 4pm ET on fire date
    snap_1h_at      timestamptz,                -- when 1h snapshot was stamped
    snap_4h_at      timestamptz,                -- when 4h snapshot was stamped
    snap_eod_at     timestamptz                 -- when EOD snapshot was stamped
);

create index entry_log_fired_at_idx  on entry_log (fired_at desc);
create index entry_log_symbol_idx    on entry_log (symbol);
create index entry_log_outcome_idx   on entry_log (outcome);
create index entry_log_model_idx     on entry_log (model);


-- ── Archive table ─────────────────────────────────────────────────────────────
-- Receives rows from entry_log in two cases:
--   1. Archive job (runs every 2 days): moves rows fired ≥ 1 full day ago
--   2. "Clear Log" action: archives ALL current rows before deletion
-- Rows here are permanent — never deleted by any UI action.

create table if not exists entry_log_archive (
    -- Same columns as entry_log
    id              bigint          primary key,  -- preserves original id
    fired_at        timestamptz     not null,
    symbol          text            not null,
    model           text            not null,
    side            text            not null,
    hour_et         integer,
    daily_bias      text,
    entry           numeric,
    stop            numeric,
    t1              numeric,
    target          numeric,
    last_price      numeric,
    outcome         text,
    outcome_at      timestamptz,
    pnl_pts         numeric,
    price_1h        numeric,
    price_4h        numeric,
    price_eod       numeric,
    snap_1h_at      timestamptz,
    snap_4h_at      timestamptz,
    snap_eod_at     timestamptz,

    -- ── Archive metadata ─────────────────────────────────────────────────────
    archived_at     timestamptz     not null default now(),
    archive_reason  text            not null default 'scheduled'
                                                -- 'scheduled' | 'manual_clear'
);

create index entry_log_archive_fired_at_idx on entry_log_archive (fired_at desc);
create index entry_log_archive_symbol_idx   on entry_log_archive (symbol);
create index entry_log_archive_outcome_idx  on entry_log_archive (outcome);
