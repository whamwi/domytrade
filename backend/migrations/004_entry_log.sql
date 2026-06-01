-- Entry log: records every NEAR→ENTRY transition across all models (AGG/CON/WIDE/CR)
-- Used for forward-testing analysis and performance tracking.

create table if not exists entry_log (
    id          bigserial primary key,
    fired_at    timestamptz not null default now(),
    symbol      text        not null,
    model       text        not null,   -- 'AGG' | 'CON' | 'WIDE' | 'CR'
    side        text        not null,   -- 'LONG' | 'SHORT'
    entry       numeric,
    stop        numeric,
    t1          numeric,
    target      numeric,
    last_price  numeric,
    daily_bias  text,                   -- 'LONG' | 'SHORT' | null
    hour_et     integer                 -- 0-23 ET hour when signal fired
);

create index if not exists entry_log_fired_at_idx on entry_log (fired_at desc);
create index if not exists entry_log_symbol_idx   on entry_log (symbol);
