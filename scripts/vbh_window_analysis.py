#!/usr/bin/env python3
"""
vbh_window_analysis.py
----------------------
Downloads 1-hour OHLC data for AAPL and MSFT via yfinance (free, no token),
computes VBH stats (AGG / CON / WIDE) at multiple historical windows,
and exports:

  vbh_raw_hourly.csv      — raw hourly H-L ranges per symbol/date/hour_et
  vbh_levels_by_window.csv — L1/L2/L3/L4 for every symbol/model/hour_et/window
  vbh_window_delta.csv    — how much the levels shift as window grows

Run:
    python3 vbh_window_analysis.py

Requires:  pip install yfinance pandas
"""

import csv
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# ── Config ─────────────────────────────────────────────────────────────────────
SYMBOLS      = ['AAPL', 'MSFT']
WINDOWS_DAYS = [90, 180, 365, 500, 730]   # compare these windows
RTH_HOURS    = list(range(9, 16))          # 9 AM → 3 PM ET (hours 9-15)
ALL_HOURS    = list(range(24))

ET = ZoneInfo('America/New_York')

# VBH formula constants (same as vbh_updater.py)
SIGMA_CAP_PCT = 0.1473   # σ_eff = min(σ, μ × 14.73%)
L4_RATIO      = 0.385    # T2 target: L4 = L1 - 0.385·σ_eff

SHIFTS = {
    'AGG' : 0.0,
    'CON' : 2.4,
    'WIDE': 4.0,
}


# ── Data fetch ──────────────────────────────────────────────────────────────────

def fetch_hourly(symbol: str, days: int = 730) -> pd.DataFrame:
    """Download 1-hour bars from Yahoo Finance (max 730 days)."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=min(days, 730))   # Yahoo caps at 730d for 1h
    print(f'  Fetching {symbol} 1h data  {start.date()} → {end.date()} ...')
    ticker = yf.Ticker(symbol)
    # yfinance caps 1h at ~730 days; use period='2y' for max available
    # prepost=False → RTH only (9:30–16:00 ET) — avoids mixing pre/post into hourly buckets
    df = ticker.history(period='2y',
                        interval='1h',
                        auto_adjust=True,
                        prepost=False)
    if df.empty:
        print(f'  WARNING: no data returned for {symbol}')
        return pd.DataFrame()

    # Normalise index to ET timezone
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize('UTC')
    df.index = df.index.tz_convert(ET)
    df['hour_et'] = df.index.hour
    df['date_et'] = df.index.date
    df['range']   = df['High'] - df['Low']
    print(f'  → {len(df)} bars  ({df.index[0].date()} → {df.index[-1].date()})')
    return df


# ── VBH computation ─────────────────────────────────────────────────────────────

def compute_vbh(ranges: list[float], shift: float) -> tuple[float, float, float, float] | None:
    """Compute (L1, L2, L3, L4) from a list of hourly H-L ranges."""
    n = len(ranges)
    if n < 3:
        return None
    mu    = sum(ranges) / n
    var   = sum((x - mu) ** 2 for x in ranges) / (n - 1)
    sigma = math.sqrt(var)

    sigma_eff = min(sigma, mu * SIGMA_CAP_PCT)

    l2 = mu + shift * sigma_eff
    l1 = max(l2 - sigma_eff, 0.0)
    l3 = l2 + sigma_eff
    l4 = max(l1 - sigma_eff * L4_RATIO, 0.0)
    return round(l1, 5), round(l2, 5), round(l3, 5), round(l4, 5)


def stats_for_window(df: pd.DataFrame, window_days: int) -> dict:
    """Return {hour_et: {model: (L1,L2,L3,L4)}} for a trailing window."""
    cutoff = df.index.max() - pd.Timedelta(days=window_days)
    subset = df[df.index >= cutoff]

    hour_ranges: dict[int, list[float]] = defaultdict(list)
    for _, row in subset.iterrows():
        r = row['range']
        if r > 0:
            hour_ranges[int(row['hour_et'])].append(float(r))

    result: dict = {}
    for h in ALL_HOURS:
        rs = hour_ranges.get(h, [])
        result[h] = {}
        for model, shift in SHIFTS.items():
            lvl = compute_vbh(rs, shift)
            result[h][model] = lvl   # None if < 3 samples
    return result


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    raw_rows    = []   # for vbh_raw_hourly.csv
    level_rows  = []   # for vbh_levels_by_window.csv
    delta_rows  = []   # for vbh_window_delta.csv

    all_stats: dict[str, dict[int, dict[int, dict]]] = {}  # symbol → window → hour → model → levels

    for symbol in SYMBOLS:
        print(f'\n{"="*60}')
        print(f' {symbol}')
        print(f'{"="*60}')

        df = fetch_hourly(symbol, days=730)
        if df.empty:
            continue

        # ── Raw hourly ranges CSV ──────────────────────────────────────────
        for _, row in df.iterrows():
            raw_rows.append({
                'symbol'  : symbol,
                'date_et' : row['date_et'],
                'hour_et' : int(row['hour_et']),
                'open'    : round(float(row['Open']),  4),
                'high'    : round(float(row['High']),  4),
                'low'     : round(float(row['Low']),   4),
                'close'   : round(float(row['Close']), 4),
                'range'   : round(float(row['range']), 5),
            })

        # ── Stats per window ───────────────────────────────────────────────
        all_stats[symbol] = {}
        for w in WINDOWS_DAYS:
            stats = stats_for_window(df, w)
            all_stats[symbol][w] = stats

            # How many samples does this window actually cover per RTH hour?
            cutoff = df.index.max() - pd.Timedelta(days=w)
            subset = df[df.index >= cutoff]
            samples_by_hour = subset.groupby('hour_et').size().to_dict()

            for h in ALL_HOURS:
                n = samples_by_hour.get(h, 0)
                for model in SHIFTS:
                    lvl = stats[h].get(model)
                    row = {
                        'symbol'      : symbol,
                        'window_days' : w,
                        'hour_et'     : h,
                        'model'       : model,
                        'samples'     : n,
                        'l1'          : lvl[0] if lvl else '',
                        'l2'          : lvl[1] if lvl else '',
                        'l3'          : lvl[2] if lvl else '',
                        'l4'          : lvl[3] if lvl else '',
                    }
                    level_rows.append(row)

        # ── Delta analysis (RTH hours only) ───────────────────────────────
        print(f'\n  VBH L1 comparison across windows (RTH hours 9-15):')
        print(f'  {"Hour":>4}  {"Model":>5}  ', end='')
        for w in WINDOWS_DAYS:
            print(f' {w:>6}d', end='')
        print(f'  {"Max-Min":>8}  {"% drift":>8}')
        print('  ' + '-' * (4 + 5 + 2 + len(WINDOWS_DAYS) * 8 + 22))

        for h in RTH_HOURS:
            for model in SHIFTS:
                vals = []
                for w in WINDOWS_DAYS:
                    lvl = all_stats[symbol][w][h].get(model)
                    vals.append(lvl[0] if lvl else None)

                vals_valid = [v for v in vals if v is not None]
                if not vals_valid:
                    continue

                spread  = max(vals_valid) - min(vals_valid)
                pct     = spread / vals_valid[0] * 100 if vals_valid[0] else 0
                base    = vals_valid[0]

                print(f'  h{h:02d}   {model:>5}  ', end='')
                for v in vals:
                    print(f' {v:>7.3f}' if v is not None else f'  {"—":>6}', end='')
                print(f'   {spread:>7.3f}   {pct:>7.1f}%')

                delta_rows.append({
                    'symbol'  : symbol,
                    'hour_et' : h,
                    'model'   : model,
                    **{f'l1_{w}d': (all_stats[symbol][w][h].get(model) or [None])[0]
                       for w in WINDOWS_DAYS},
                    'spread'  : round(spread, 5),
                    'pct_drift': round(pct, 2),
                })

    # ── Write CSVs ─────────────────────────────────────────────────────────
    out_dir = '/Users/wassim/domytrade/scripts'

    def write_csv(path, rows, fieldnames):
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f'\nWrote {len(rows):,} rows → {path}')

    write_csv(f'{out_dir}/vbh_raw_hourly.csv', raw_rows,
              ['symbol','date_et','hour_et','open','high','low','close','range'])

    write_csv(f'{out_dir}/vbh_levels_by_window.csv', level_rows,
              ['symbol','window_days','hour_et','model','samples','l1','l2','l3','l4'])

    write_csv(f'{out_dir}/vbh_window_delta.csv', delta_rows,
              ['symbol','hour_et','model',
               *[f'l1_{w}d' for w in WINDOWS_DAYS],
               'spread','pct_drift'])

    print('\nDone.')


if __name__ == '__main__':
    main()
