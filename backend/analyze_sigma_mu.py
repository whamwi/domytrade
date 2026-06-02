#!/usr/bin/env python3
"""
analyze_sigma_mu.py
-------------------
Compare 2022 hardcoded VBH σ/μ ratios vs actual 2026 uncapped σ/μ ratios from DB.

For AGG model:
  L2 = μ,  L1 = μ - σ  →  σ = L2 - L1
  ratio_2022 = (L2 - L1) / L2  = σ/μ  per (symbol, hour)

2026 uncapped: raw σ from sample std dev of (high - low) per hour_et over last 90 days.
"""

import sys
import os
sys.path.insert(0, '/Users/wassim/domytrade/backend')

from dotenv import load_dotenv
load_dotenv('/Users/wassim/domytrade/backend/.env')

from collections import defaultdict
import statistics

from db import get_db
from vbh_engine import _AGG_2022

RTH_HOURS = list(range(9, 17))  # 9..16 inclusive


# ─── 1. Extract 2022 ratios from _AGG_2022 ───────────────────────────────────
# Skip symbols with None data (aliases like /MES, /MNQ etc)
# For AGG: L1 = μ − σ, L2 = μ  →  σ/μ = (L2 − L1) / L2

ratios_2022: dict[str, dict[int, float]] = {}  # {ticker: {hour_et: ratio}}

for ticker, data in _AGG_2022.items():
    if data is None:
        continue
    ratios_2022[ticker] = {}
    for h, (l1, l2, l3, l4) in enumerate(data):
        if l2 > 0:
            ratios_2022[ticker][h] = (l2 - l1) / l2

print(f"2022 symbols with data: {sorted(ratios_2022.keys())}")
print()


# ─── 2. Query DB for 2026 uncapped σ/μ per (symbol, hour_et) ─────────────────
# Fetch all ohlc_hourly rows for active futures symbols (last 90 days via get_ohlc)
# Compute uncapped σ/μ: sample std dev / mean of (high - low) per hour

db = get_db()

# Get active futures symbols (those that start with '/')
all_syms = (db.table('symbols')
              .select('id,ticker')
              .eq('is_active', True)
              .execute().data)
futures_syms = [s for s in all_syms if s['ticker'].startswith('/')]

print(f"Active futures in DB: {[s['ticker'] for s in futures_syms]}")
print()

from datetime import datetime, timezone, timedelta
cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

# Collect all ranges per (ticker, hour_et)
hour_ranges_2026: dict[str, dict[int, list[float]]] = {}

for sym in futures_syms:
    ticker = sym['ticker']
    sid    = sym['id']

    # Fetch 90-day ohlc_hourly — paginate in case > 1000 rows
    PAGE = 1000
    all_rows = []
    offset = 0
    while True:
        res = (db.table('ohlc_hourly')
                 .select('hour_et,high,low')
                 .eq('symbol_id', sid)
                 .gte('bar_time', cutoff)
                 .range(offset, offset + PAGE - 1)
                 .execute())
        all_rows.extend(res.data)
        if len(res.data) < PAGE:
            break
        offset += PAGE

    if not all_rows:
        continue

    hour_ranges_2026[ticker] = defaultdict(list)
    for row in all_rows:
        r = row['high'] - row['low']
        if r > 0:
            hour_ranges_2026[ticker][row['hour_et']].append(r)


# Compute uncapped σ/μ per (ticker, hour)
ratios_2026: dict[str, dict[int, float]] = {}

for ticker, hr_map in hour_ranges_2026.items():
    ratios_2026[ticker] = {}
    for h, rs in hr_map.items():
        if len(rs) >= 3:
            mu    = sum(rs) / len(rs)
            sigma = statistics.stdev(rs)  # sample std dev
            if mu > 0:
                ratios_2026[ticker][h] = sigma / mu


# ─── 3. Comparison table ─────────────────────────────────────────────────────
# For each symbol that has both 2022 and 2026 data, show RTH (9–16) summary

# Find common symbols (base ticker without exchange suffix)
def base(t: str) -> str:
    return t.split(':')[0]

# Map base ticker → 2026 ticker (may have exchange suffix in DB)
base_to_2026 = {}
for t in ratios_2026:
    base_to_2026[base(t)] = t

print("=" * 90)
print(f"{'Symbol':<8} | {'Med σ/μ 2022':>13} | {'Med σ/μ 2026':>13} | {'Cap% → match 2022':>18} | {'Obs 2026':>8}")
print("=" * 90)

all_r22_rth = []
all_r26_rth = []

results = []

for ticker_2022 in sorted(ratios_2022.keys()):
    b = base(ticker_2022)
    if b not in base_to_2026:
        print(f"{ticker_2022:<8} | {'(no 2026 data)':>13}")
        continue

    ticker_2026 = base_to_2026[b]
    r22 = ratios_2022[ticker_2022]
    r26 = ratios_2026.get(ticker_2026, {})

    # RTH only
    vals_22 = [r22[h] for h in RTH_HOURS if h in r22]
    vals_26 = [r26[h] for h in RTH_HOURS if h in r26]

    if not vals_22 or not vals_26:
        print(f"{ticker_2022:<8} | {'(insufficient data)':>13}")
        continue

    med_22 = statistics.median(vals_22)
    med_26 = statistics.median(vals_26)

    # What cap % of μ would reproduce 2022's median σ_eff?
    # cap_pct is the number x such that min(σ, μ*x) = med_22_abs level
    # In ratio terms: cap_ratio = med_22 / med_26  (if med_26 > med_22, cap needed)
    implied_cap_pct = med_22 * 100   # if we set cap = med_22 * μ → this ratio %

    all_r22_rth.extend(vals_22)
    all_r26_rth.extend(vals_26)

    results.append((ticker_2022, med_22, med_26, implied_cap_pct, len(vals_26)))

    print(f"{ticker_2022:<8} | {med_22*100:>12.1f}% | {med_26*100:>12.1f}% | {implied_cap_pct:>17.1f}% | {len(vals_26):>8}")

print("=" * 90)

if all_r22_rth and all_r26_rth:
    overall_med_22 = statistics.median(all_r22_rth)
    overall_med_26 = statistics.median(all_r26_rth)
    print(f"{'ALL RTH':<8} | {overall_med_22*100:>12.1f}% | {overall_med_26*100:>12.1f}% | {overall_med_22*100:>17.1f}% |")
    print()
    print(f"  → 2022 median σ/μ across all symbols/RTH hours: {overall_med_22*100:.1f}%")
    print(f"  → 2026 median σ/μ (UNCAPPED) across all symbols/RTH hours: {overall_med_26*100:.1f}%")
    print(f"  → A cap of ~{overall_med_22*100:.0f}% would bring median 2026 down to match 2022 median")
    print(f"     (current cap in code is 20%)")


# ─── 4. Per-symbol detail: distribution across RTH hours ─────────────────────
print()
print("=" * 90)
print("PER-SYMBOL RTH HOUR DETAIL  (σ/μ ratio, 2022 vs 2026 uncapped)")
print("=" * 90)

for ticker_2022 in sorted(ratios_2022.keys()):
    b = base(ticker_2022)
    if b not in base_to_2026:
        continue
    ticker_2026 = base_to_2026[b]
    r22 = ratios_2022[ticker_2022]
    r26 = ratios_2026.get(ticker_2026, {})

    vals_22 = [r22.get(h) for h in RTH_HOURS]
    vals_26 = [r26.get(h) for h in RTH_HOURS]

    if not any(v for v in vals_22) or not any(v for v in vals_26):
        continue

    print(f"\n{ticker_2022}")
    print(f"  {'Hour':<6} {'σ/μ 2022':>10} {'σ/μ 2026':>10} {'Δ':>8} {'n 2026':>8}")
    print(f"  {'-'*44}")
    for h in RTH_HOURS:
        v22 = r22.get(h)
        v26 = r26.get(h)
        n26 = len(hour_ranges_2026.get(ticker_2026, {}).get(h, []))
        if v22 is not None and v26 is not None:
            delta = v26 - v22
            print(f"  {h:<6} {v22*100:>9.1f}% {v26*100:>9.1f}% {delta*100:>+7.1f}% {n26:>8}")
        elif v22 is not None:
            print(f"  {h:<6} {v22*100:>9.1f}% {'  (no data)':>10}")


# ─── 5. Threshold analysis: how many (symbol, hour) pairs exceed each cap ────
print()
print("=" * 90)
print("THRESHOLD ANALYSIS: % of (symbol, RTH hour) pairs with σ/μ 2026 > threshold")
print("=" * 90)

all_pairs_2026 = []
for ticker_2026 in ratios_2026:
    r26 = ratios_2026[ticker_2026]
    # Only include if corresponding 2022 base exists
    b = base(ticker_2026)
    if b not in ratios_2022:
        continue
    for h in RTH_HOURS:
        if h in r26:
            all_pairs_2026.append((ticker_2026, h, r26[h]))

total = len(all_pairs_2026)
print(f"\nTotal (symbol, RTH hour) pairs with 2026 data: {total}")
print()
print(f"  {'Threshold':>12} | {'Count > thresh':>15} | {'%':>8}")
print(f"  {'-'*40}")
for thresh in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
    n = sum(1 for _, _, r in all_pairs_2026 if r > thresh)
    pct = 100 * n / total if total else 0
    print(f"  {thresh*100:>11.0f}% | {n:>15} | {pct:>7.1f}%")

print()
print("Done.")
