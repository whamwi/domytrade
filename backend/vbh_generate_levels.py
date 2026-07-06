"""
VBH Level Generator — compute TOS-equivalent constants from historical OHLC data.

Formula (CON model):
    L1 = p85(hourly H-L over 20 weeks) × cal_factor[ticker][hour]
    L2 = L1 × 1.155
    L3 = L1 × 1.310
    L4 = L1 × 0.935

    AGG L1 = CON L1 × model_ratio[ticker]['AGG']          (futures: scalar per ticker)
    WIDE L1 = CON L1 × model_ratio[ticker]['WIDE']         (futures only; stocks have no WIDE)

    For stocks, AGG ratios are per-hour (vary significantly across RTH hours).

Accuracy: ±5–10% vs TOS for equity index futures RTH hours.
          ±5% for MSFT, ±5–10% for AAPL (hr9, hr13, hr15 more variable).
          Higher error on commodities (CL, GC, SI) afternoon hours.

Calibration factors were derived by fitting 3 known TOS weeks
(2026-06-14, 2026-06-21, 2026-06-28). The systematic gap exists because
TOS uses ~52-week lookback including Liberation Day (Apr 2025), while
our DB only goes back to Sep 2025. Re-run --recalibrate after each new
TOS import to keep factors fresh.

Usage:
    # Generate next week's levels (auto-detects next Sunday)
    python3 vbh_generate_levels.py

    # Preview without upserting
    python3 vbh_generate_levels.py --dry-run

    # Specific week
    python3 vbh_generate_levels.py --week_of 2026-07-05

    # Re-derive calibration factors from all TOS rows in DB, then print
    python3 vbh_generate_levels.py --recalibrate
"""

import argparse
import os
from datetime import date, timedelta
from supabase import create_client
import httpx

# ── calibration factors ───────────────────────────────────────────────────────
# Factor = exponentially-weighted avg of (TOS_L1 / p85_20w) across known TOS weeks.
# Weights: most-recent 50%, one-week-prior 30%, two-weeks-prior 20%.
# Recent weeks dominate because vol regimes drift (end-of-quarter, gold surge, etc.).
# Afternoon hours (12-13) still carry larger factors from the mid-day vol structure.
# Re-run --recalibrate after each new TOS import to keep factors fresh.
CAL_FACTORS: dict[str, dict[int, float]] = {
    '/ES':  {9: 1.142, 10: 1.154, 11: 1.220, 12: 1.371, 13: 1.445, 14: 1.213, 15: 1.324},
    '/NQ':  {9: 1.217, 10: 1.241, 11: 1.322, 12: 1.511, 13: 1.502, 14: 1.254, 15: 1.272},
    '/YM':  {9: 1.063, 10: 1.147, 11: 1.160, 12: 1.116, 13: 1.327, 14: 1.183, 15: 1.286},
    '/RTY': {9: 1.131, 10: 1.170, 11: 1.218, 12: 1.247, 13: 1.405, 14: 1.254, 15: 1.300},
    '/GC':  {9: 1.059, 10: 0.929, 11: 0.908, 12: 0.957, 13: 1.365, 14: 1.485, 15: 1.330},
    '/CL':  {9: 0.986, 10: 1.015, 11: 0.984, 12: 1.034, 13: 1.225, 14: 0.785, 15: 0.963},
    '/ZB':  {9: 0.958, 10: 1.060, 11: 1.013, 12: 1.154, 13: 1.391, 14: 1.169, 15: 1.211},
    '/SI':  {9: 0.951, 10: 0.967, 11: 0.846, 12: 0.773, 13: 1.193, 14: 1.224, 15: 1.133},
}

# ── calibration factors — STOCKS ──────────────────────────────────────────────
# Same 50/30/20 exponential weighting as futures.
# Stocks often have cal_factors ≤ 1 for morning hours: our recent DB is noisier
# than TOS's longer lookback for those early hours.
CAL_FACTORS_STOCKS: dict[str, dict[int, float]] = {
    'AAPL': {9: 0.973, 10: 0.974, 11: 0.986, 12: 0.990, 13: 1.358, 14: 1.226, 15: 1.109},
    'MSFT': {9: 0.986, 10: 1.107, 11: 0.913, 12: 1.021, 13: 1.069, 14: 1.138, 15: 1.175},
}

# AGG/CON and WIDE/CON L1 ratios (avg across 3 TOS weeks, 7 RTH hours per symbol)
# Futures: single scalar per ticker.
# Stocks: per-hour dict — the ratio varies significantly across RTH hours for equities
#         (AAPL hr13 AGG/CON = 0.45 vs hr9 = 0.67; no WIDE model for stocks).
MODEL_RATIOS: dict[str, dict[str, float | dict[int, float]]] = {
    '/ES':  {'AGG': 0.628, 'WIDE': 1.100},
    '/NQ':  {'AGG': 0.712, 'WIDE': 1.243},
    '/YM':  {'AGG': 0.657, 'WIDE': 1.101},
    '/RTY': {'AGG': 0.654, 'WIDE': 1.099},
    '/GC':  {'AGG': 0.615, 'WIDE': 1.275},
    '/CL':  {'AGG': 0.612, 'WIDE': 1.388},
    '/ZB':  {'AGG': 0.647, 'WIDE': 1.073},
    '/SI':  {'AGG': 0.615, 'WIDE': 1.275},
    'AAPL': {'AGG': {9: 0.671, 10: 0.648, 11: 0.686, 12: 0.632, 13: 0.448, 14: 0.493, 15: 0.596}},
    'MSFT': {'AGG': {9: 0.705, 10: 0.614, 11: 0.700, 12: 0.690, 13: 0.634, 14: 0.656, 15: 0.609}},
}

# Inter-level multipliers — exact structure derived from ThinkScript ratio analysis
L2_MULT = 1.155   # L2 / L1 (midpoint of box cloud)
L3_MULT = 1.310   # L3 / L1 (outer cloud / stop level)
L4_MULT = 0.935   # L4 / L1 (T2 target level, slightly inside entry)

RTH_HOURS = list(range(9, 16))   # 9 AM–3 PM ET (stocks: hr9 = first 30 min 9:30–10:00)
LOOKBACK_WEEKS = 20

# Merged lookup used by _build_rows
_ALL_CAL_FACTORS = {**CAL_FACTORS, **CAL_FACTORS_STOCKS}

# ── connection ────────────────────────────────────────────────────────────────
env: dict[str, str] = {}
with open('/Users/wassim/domytrade/backend/.env') as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

sb = create_client(env['SUPABASE_URL'], env['SUPABASE_SERVICE_ROLE_KEY'])

# ── symbol lookup ─────────────────────────────────────────────────────────────
_syms = sb.table('symbols').select('id,ticker').execute().data
_sym_by_ticker = {s['ticker']: s['id'] for s in _syms}


# ── helpers ───────────────────────────────────────────────────────────────────
def next_week_start(today: date | None = None) -> date:
    """Return the next Sunday (week_of convention matches TOS: Jun 28, Jul 5, etc.)."""
    today = today or date.today()
    days = (6 - today.weekday()) % 7   # days until Sunday
    return today + timedelta(days=days if days else 7)


def _p85_20w(symbol_id: int, hour_et: int, week_of: date) -> float | None:
    """Query p85 of hourly H-L over the 20 weeks preceding week_of."""
    lookback_start = week_of - timedelta(weeks=LOOKBACK_WEEKS)
    result = sb.rpc('query_p85_hl', {
        'p_symbol_id': symbol_id,
        'p_hour_et':   hour_et,
        'p_start':     lookback_start.isoformat(),
        'p_end':       week_of.isoformat(),
    }).execute()
    # rpc doesn't exist yet — fall back to execute_sql via REST
    # Instead, read directly via Supabase filter (can't do percentile via REST)
    # Use raw SQL via the mcp tool or psycopg2; for now raise if missing
    raise NotImplementedError("Use --recalibrate flow or direct psycopg2 connection")


def p85_via_rows(symbol_id: int, hour_et: int, week_of: date) -> float | None:
    """Fetch H-L values and compute p85 in Python (Supabase REST-compatible)."""
    lookback_start = (week_of - timedelta(weeks=LOOKBACK_WEEKS)).isoformat()
    week_of_str = week_of.isoformat()

    rows = (
        sb.table('ohlc_hourly')
        .select('high,low')
        .eq('symbol_id', symbol_id)
        .eq('hour_et', hour_et)
        .gte('bar_time', lookback_start)
        .lt('bar_time', week_of_str)
        .execute()
        .data
    )
    if not rows:
        return None

    ranges = sorted(float(r['high']) - float(r['low']) for r in rows)
    n = len(ranges)
    # percentile_cont(0.85) — linear interpolation
    idx = 0.85 * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return ranges[lo] + (ranges[hi] - ranges[lo]) * (idx - lo)


def _derive_levels(l1: float) -> tuple[float, float, float]:
    """Return (L2, L3, L4) from L1 using fixed multipliers."""
    return l1 * L2_MULT, l1 * L3_MULT, l1 * L4_MULT


def _build_rows(ticker: str, symbol_id: int, week_of: date) -> list[dict]:
    """Compute all model rows for one ticker, one week."""
    rows: list[dict] = []
    cal_hours = _ALL_CAL_FACTORS.get(ticker, {})
    ratios = MODEL_RATIOS.get(ticker, {})

    for hour_et in RTH_HOURS:
        cal = cal_hours.get(hour_et)
        if cal is None:
            continue

        p85 = p85_via_rows(symbol_id, hour_et, week_of)
        if p85 is None:
            print(f"  WARNING {ticker} hr{hour_et}: no historical bars — skipping")
            continue

        l1_con = p85 * cal

        models_to_gen = [('CON', l1_con)]
        agg = ratios.get('AGG')
        if agg is not None:
            r = agg.get(hour_et, 1.0) if isinstance(agg, dict) else agg
            models_to_gen.append(('AGG', l1_con * r))
        wide = ratios.get('WIDE')
        if wide is not None:
            r = wide.get(hour_et, 1.0) if isinstance(wide, dict) else wide
            models_to_gen.append(('WIDE', l1_con * r))

        for model, l1 in models_to_gen:
            ml2, ml3, ml4 = _derive_levels(l1)
            rows.append({
                'symbol_id': symbol_id,
                'model':     model,
                'hour_et':   hour_et,
                'l1':        round(l1,  5),
                'l2':        round(ml2, 5),
                'l3':        round(ml3, 5),
                'l4':        round(ml4, 5),
            })

    return rows


def _upsert(rows: list[dict], week_of: date) -> None:
    """Upsert into vbh_stats (live dashboard). vbh_ts_weekly is TOS-only; never written here."""
    for i in range(0, len(rows), 50):
        sb.table('vbh_stats').upsert(
            rows[i:i + 50],
            on_conflict='symbol_id,model,hour_et',
        ).execute()


def hot_reload() -> None:
    print("\n── Hot-reloading Railway backend …")
    try:
        r = httpx.post(
            'https://domytrade-backend-production.up.railway.app/api/reload-db-stats',
            timeout=30,
        )
        print(f"  {r.json()}")
    except Exception as e:
        print(f"  Hot-reload failed: {e}")


# ── recalibrate ───────────────────────────────────────────────────────────────
def recalibrate(known_weeks: list[str] | None = None) -> None:
    """Re-derive CAL_FACTORS from DB rows. Prints updated dict; edit manually."""
    print("\n── Recalibrating from DB …")
    # Fetch all rows from vbh_ts_weekly for known TOS weeks
    if not known_weeks:
        known_weeks = ['2026-06-14', '2026-06-21', '2026-06-28', '2026-07-05']

    print(f"  Using weeks: {known_weeks}")
    tos_rows: list[dict] = []
    for w in known_weeks:
        rows = (
            sb.table('vbh_ts_weekly')
            .select('symbol_id,model,hour_et,l1')
            .eq('week_of', w)
            .eq('model', 'CON')
            .execute()
            .data
        )
        for r in rows:
            r['week_of'] = w
        tos_rows.extend(rows)

    # Fetch symbol map
    syms = {s['id']: s['ticker'] for s in _syms}

    # Group by (ticker, hour_et): ordered list of (week_of, ratio) — oldest first
    from collections import defaultdict
    ratio_by_week: dict[str, dict[int, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))

    for r in tos_rows:
        ticker = syms.get(r['symbol_id'])
        if not ticker or ticker not in _ALL_CAL_FACTORS:
            continue
        hour_et = r['hour_et']
        week_of = date.fromisoformat(r['week_of'])
        tos_l1 = float(r['l1'])

        p85 = p85_via_rows(r['symbol_id'], hour_et, week_of)
        if p85 and p85 > 0:
            ratio_by_week[ticker][hour_et].append((r['week_of'], tos_l1 / p85))

    # Exponential weights: most recent = 0.50, previous = 0.30, oldest = 0.20
    # Generalised for N weeks: weight[i] decays with i=0 = most recent.
    def _weighted_avg(vals: list[tuple[str, float]]) -> float:
        ordered = [r for _, r in sorted(vals, key=lambda x: x[0], reverse=True)]
        n = len(ordered)
        if n == 1:
            return ordered[0]
        if n == 2:
            return 0.6 * ordered[0] + 0.4 * ordered[1]
        # 3+: 50/30/20 for the three most recent; equal-weight remainder
        base_w = [0.50, 0.30, 0.20]
        if n > 3:
            tail_w = (1.0 - sum(base_w)) / (n - 3)
            weights = base_w + [tail_w] * (n - 3)
        else:
            weights = base_w[:n]
        return sum(w * v for w, v in zip(weights, ordered))

    print("\nCAL_FACTORS = {")
    for ticker in sorted(ratio_by_week):
        hours = ratio_by_week[ticker]
        print(f"    '{ticker}': {{", end='')
        parts = []
        for h in sorted(hours):
            avg = _weighted_avg(hours[h])
            parts.append(f"{h}: {avg:.3f}")
        print(', '.join(parts) + '},')
    print("}")


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description='Generate VBH constants from historical OHLC data')
    parser.add_argument('--week_of', help='Week start YYYY-MM-DD (default: next Monday)')
    parser.add_argument('--dry-run', action='store_true', help='Print levels without upserting')
    parser.add_argument('--recalibrate', action='store_true', help='Recompute calibration factors from DB')
    parser.add_argument('--tickers', nargs='+', help='Limit to specific tickers (default: all)')
    args = parser.parse_args()

    if args.recalibrate:
        recalibrate()
        return

    week_of = date.fromisoformat(args.week_of) if args.week_of else next_week_start()
    tickers = args.tickers or list(_ALL_CAL_FACTORS.keys())

    print(f"── VBH Level Generator — week_of {week_of} {'(DRY RUN)' if args.dry_run else ''}")
    print(f"   Tickers: {tickers}")
    print(f"   Lookback: p85 of {LOOKBACK_WEEKS}-week hourly H-L × calibration factor\n")

    all_rows: list[dict] = []
    for ticker in tickers:
        symbol_id = _sym_by_ticker.get(ticker)
        if not symbol_id:
            print(f"  SKIP {ticker} — not in symbols table")
            continue

        rows = _build_rows(ticker, symbol_id, week_of)
        con_rows = [r for r in rows if r['model'] == 'CON']

        # Summary print
        print(f"  {ticker}:")
        for r in con_rows:
            print(f"    hr{r['hour_et']:02d}  L1={r['l1']:8.3f}  L2={r['l2']:8.3f}  L3={r['l3']:8.3f}  L4={r['l4']:8.3f}")

        all_rows.extend(rows)

    if not args.dry_run and all_rows:
        print(f"\n── Upserting {len(all_rows)} rows …")
        _upsert(all_rows, week_of)
        print("── Done.")
        hot_reload()
    elif args.dry_run:
        print(f"\n[DRY RUN] Would upsert {len(all_rows)} rows — pass without --dry-run to commit.")


if __name__ == '__main__':
    main()
