-- ─────────────────────────────────────────────────────────────────────────────
-- 006 — Tiered targets (T1/T2/T3) for entry_log + entry_log_archive
--
-- Adds per-tier target levels, P&L-in-points, hit timestamps, and per-tier
-- outcomes so each entry can be scaled out across AGG → CON → WIDE levels.
--
-- Model fired → tiers tracked:
--   AGG   → T1 only
--   CON   → T1 (AGG target) + T2 (CON target)
--   WIDE  → T1 (AGG target) + T2 (CON target) + T3 (WIDE target)
--   CR    → T1 only  (CR uses its own geometry)
--
-- outcome field now reflects the furthest tier hit:
--   'OPEN' | 'HIT_T1' | 'HIT_T2' | 'HIT_T3' | 'HIT_STOP' | 'EXPIRED'
-- ─────────────────────────────────────────────────────────────────────────────

-- ── entry_log ─────────────────────────────────────────────────────────────────
alter table entry_log
  add column if not exists target_t1   numeric,       -- AGG target price
  add column if not exists target_t2   numeric,       -- CON target price (null for AGG)
  add column if not exists target_t3   numeric,       -- WIDE target price (null for AGG/CON)
  add column if not exists pnl_t1      numeric,       -- pts entry → T1 (fixed at fire time)
  add column if not exists pnl_t2      numeric,       -- pts entry → T2
  add column if not exists pnl_t3      numeric,       -- pts entry → T3
  add column if not exists outcome_t1  text,          -- 'HIT' | 'OPEN' | null
  add column if not exists outcome_t2  text,          -- 'HIT' | 'OPEN' | null
  add column if not exists outcome_t3  text,          -- 'HIT' | 'OPEN' | null
  add column if not exists t1_hit_at   timestamptz,   -- when T1 was crossed
  add column if not exists t2_hit_at   timestamptz,   -- when T2 was crossed
  add column if not exists t3_hit_at   timestamptz;   -- when T3 was crossed

-- ── entry_log_archive (mirror) ────────────────────────────────────────────────
alter table entry_log_archive
  add column if not exists target_t1   numeric,
  add column if not exists target_t2   numeric,
  add column if not exists target_t3   numeric,
  add column if not exists pnl_t1      numeric,
  add column if not exists pnl_t2      numeric,
  add column if not exists pnl_t3      numeric,
  add column if not exists outcome_t1  text,
  add column if not exists outcome_t2  text,
  add column if not exists outcome_t3  text,
  add column if not exists t1_hit_at   timestamptz,
  add column if not exists t2_hit_at   timestamptz,
  add column if not exists t3_hit_at   timestamptz;
