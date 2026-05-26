#!/usr/bin/env python3
"""
update_vbh_tables.py
--------------------
Fetches 30-min Schwab candles for active futures symbols, aggregates them into
hourly H/L buckets, persists to ohlc_hourly in Supabase, then computes per-hour
ATR means, applies confirmed VBH k-ratios, and upserts to vbh_stats.

This replaces the old approach of patching vbh_engine.py in-place.
The live app reads stats from the DB via vbh_engine.load_stats_from_db().

Usage:
    python3 update_vbh_tables.py            # all active future symbols
    python3 update_vbh_tables.py /ES /NQ    # specific symbols only
"""

import json, sys, time, requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# ── Constants ─────────────────────────────────────────────────────────────────

TOKEN_PATH   = '/Users/wassim/token.json'
LOOKBACK_DAYS = 90
ET            = ZoneInfo('America/New_York')

# Confirmed k-ratios from 3-week TOS study cross-week analysis
K_AGG = (0.8527, 1.0000, 1.1473, 0.7960)   # L1, L2, L3, L4 — Aggressive (~1σ)
K_CON = (0.7054, 1.0000, 1.2946, 0.5920)   # L1, L2, L3, L4 — Conservative (~2σ)


# ── Schwab helpers ────────────────────────────────────────────────────────────

def get_token() -> str:
    """Read the current Schwab access token from the local token.json file."""
    with open(TOKEN_PATH) as f:
        return json.load(f)['token']['access_token']


def fetch_schwab_candles(schwab_symbol: str, token: str, start_ms: int) -> list[dict] | None:
    """Fetch 30-min candles from Schwab from start_ms to now.

    Returns a list of candle dicts on success, or None on error.
    Prints a warning if the response doesn't contain candles.

    Note: strips exchange suffix (e.g. /ES:XCME → /ES) — pricehistory
    API rejects the fully-qualified form for futures.
    """
    # Strip exchange suffix if present (/ES:XCME → /ES)
    api_symbol = schwab_symbol.split(':')[0]
    end_ms = int(time.time() * 1000)
    try:
        resp = requests.get(
            'https://api.schwabapi.com/marketdata/v1/pricehistory',
            headers={'Authorization': f'Bearer {token}'},
            params={
                'symbol'               : api_symbol,
                'frequencyType'        : 'minute',
                'frequency'            : 30,
                'startDate'            : start_ms,
                'endDate'              : end_ms,
                'needExtendedHoursData': True,
            },
            timeout=20,
        )
        data = resp.json()
    except Exception as e:
        print(f'    FETCH ERROR — {e}')
        return None

    if 'candles' not in data:
        print(f'    FETCH WARNING — no candles key in response: {data}')
        return None

    return data['candles']


# ── Aggregation helpers ───────────────────────────────────────────────────────

def aggregate_to_hourly(
    symbol_id: int, candles: list[dict]
) -> list[dict]:
    """Aggregate 30-min candles into hourly OHLCV bars.

    For each UTC-floored hour bucket:
      open   = first candle's open
      high   = max of all highs
      low    = min of all lows
      close  = last candle's close
      volume = sum of all volumes

    Returns a list of rows ready for ohlc_hourly upsert.
    bar_time is UTC ISO string (minute=0, second=0).
    hour_et  is the ET hour of that bar.
    """
    # Group candles by their UTC-floored hour
    buckets: dict[datetime, dict] = {}

    for c in candles:
        # Schwab timestamps are ms-since-epoch UTC
        dt_utc = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
        # Floor to the hour in UTC
        bucket_utc = dt_utc.replace(minute=0, second=0, microsecond=0)

        if bucket_utc not in buckets:
            buckets[bucket_utc] = {
                'open'  : c['open'],
                'high'  : c['high'],
                'low'   : c['low'],
                'close' : c['close'],
                'volume': c.get('volume') or 0,
            }
        else:
            b = buckets[bucket_utc]
            if c['high'] > b['high']:
                b['high'] = c['high']
            if c['low'] < b['low']:
                b['low'] = c['low']
            b['close']   = c['close']           # always take last close in bucket
            b['volume'] += (c.get('volume') or 0)

    rows = []
    for bucket_utc, b in sorted(buckets.items()):
        # Compute ET hour for this bar
        bucket_et = bucket_utc.astimezone(ET)
        rows.append({
            'symbol_id': symbol_id,
            'bar_time' : bucket_utc.isoformat(),
            'hour_et'  : bucket_et.hour,
            'open'     : float(b['open']),
            'high'     : float(b['high']),
            'low'      : float(b['low']),
            'close'    : float(b['close']),
            'volume'   : int(b['volume']),
        })
    return rows


def compute_vbh_rows(
    symbol_id: int, ohlc_rows: list[dict]
) -> list[dict]:
    """Compute per-hour ATR means from ohlc_hourly rows and apply VBH k-ratios.

    For each ET hour (0–23):
      - Collect all H-L ranges across the 90-day window
      - If < 3 observations: upsert zeros (insufficient data)
      - Otherwise: L2 = mean range; scale by k-ratios for L1/L3/L4

    Returns a flat list of vbh_stats rows (both AGG and CON) for all 24 hours.
    """
    # Bucket ranges by ET hour
    hour_ranges: dict[int, list[float]] = defaultdict(list)
    for row in ohlc_rows:
        r = row['high'] - row['low']
        if r > 0:
            hour_ranges[row['hour_et']].append(r)

    rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for h in range(24):
        rs  = hour_ranges.get(h, [])
        obs = len(rs)

        if obs < 3:
            # Insufficient data — upsert zeros so the DB row exists
            l1 = l2 = l3 = l4 = 0.0
        else:
            l2 = sum(rs) / obs              # ATR center = mean hourly range
            l1 = round(l2 * K_AGG[0], 5)
            l3 = round(l2 * K_AGG[2], 5)
            l4 = round(l2 * K_AGG[3], 5)
            l2 = round(l2, 5)

        rows.append({
            'symbol_id'    : symbol_id,
            'model'        : 'AGG',
            'hour_et'      : h,
            'l1'           : l1,
            'l2'           : l2,
            'l3'           : l3,
            'l4'           : l4,
            'sample_count' : obs,
            'lookback_days': LOOKBACK_DAYS,
            'computed_at'  : now_iso,
        })

        # CON model — same L2 (mean range), different k-ratios
        if obs < 3:
            cl1 = cl2 = cl3 = cl4 = 0.0
        else:
            cl2_raw = sum(rs) / obs
            cl1 = round(cl2_raw * K_CON[0], 5)
            cl2 = round(cl2_raw, 5)
            cl3 = round(cl2_raw * K_CON[2], 5)
            cl4 = round(cl2_raw * K_CON[3], 5)

        rows.append({
            'symbol_id'    : symbol_id,
            'model'        : 'CON',
            'hour_et'      : h,
            'l1'           : cl1 if obs >= 3 else 0.0,
            'l2'           : cl2 if obs >= 3 else 0.0,
            'l3'           : cl3 if obs >= 3 else 0.0,
            'l4'           : cl4 if obs >= 3 else 0.0,
            'sample_count' : obs,
            'lookback_days': LOOKBACK_DAYS,
            'computed_at'  : now_iso,
        })

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Import DB helpers (requires .env to be loadable from this directory)
    from db import (get_db, upsert_ohlc, get_ohlc, upsert_vbh_stats,
                    get_last_ohlc_bar_times)

    # ── Determine target symbols ───────────────────────────────────────────────
    # If specific tickers are passed on CLI, use those; otherwise all active futures.
    cli_tickers = sys.argv[1:]
    if cli_tickers:
        # Allow shorthand: ES → /ES
        cli_tickers = [t if t.startswith('/') else f'/{t}' for t in cli_tickers]

    # Load all active symbols from DB
    all_syms = get_db().table('symbols').select('id,ticker,schwab_symbol,asset_type') \
                       .eq('is_active', True).order('id').execute().data

    # Filter to futures only (ticker starts with '/'), then apply CLI filter if given
    targets = [s for s in all_syms if s['ticker'].startswith('/')]
    if cli_tickers:
        unknown = [t for t in cli_tickers if t not in {s['ticker'] for s in targets}]
        if unknown:
            print(f'Unknown or inactive symbol(s): {unknown}')
            print(f'Active futures: {[s["ticker"] for s in targets]}')
            sys.exit(1)
        targets = [s for s in targets if s['ticker'] in cli_tickers]

    if not targets:
        print('No active futures symbols found.')
        sys.exit(0)

    print(f'Updating {len(targets)} symbol(s): {[s["ticker"] for s in targets]}')
    print(f'Lookback: {LOOKBACK_DAYS} days ending {datetime.now().strftime("%Y-%m-%d")}\n')

    # ── Get last known bar times for incremental fetch ─────────────────────────
    symbol_ids = [s['id'] for s in targets]
    last_bar_times = get_last_ohlc_bar_times(symbol_ids)

    token = get_token()
    ok, failed = [], []
    default_start_ms = int((time.time() - LOOKBACK_DAYS * 86400) * 1000)

    for sym in targets:
        sid           = sym['id']
        ticker        = sym['ticker']
        schwab_sym    = sym['schwab_symbol']

        print(f'  {ticker} (schwab={schwab_sym}) … ', end='', flush=True)

        # ── Step 2: determine incremental start time ───────────────────────────
        last_bt = last_bar_times.get(sid)
        if last_bt:
            # Resume from 1ms after the last stored bar so we don't re-fetch old data
            last_dt    = datetime.fromisoformat(last_bt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            start_ms = int(last_dt.timestamp() * 1000) + 1
        else:
            # First run for this symbol — go back the full lookback window
            start_ms = default_start_ms

        # ── Step 3: fetch 30-min candles from Schwab ───────────────────────────
        candles = fetch_schwab_candles(schwab_sym, token, start_ms)
        if not candles:
            print('SKIP — no candles returned')
            failed.append(ticker)
            time.sleep(0.5)
            continue

        # ── Step 4: aggregate 30-min → hourly buckets ─────────────────────────
        hourly_rows = aggregate_to_hourly(sid, candles)
        if not hourly_rows:
            print('SKIP — no hourly rows after aggregation')
            failed.append(ticker)
            time.sleep(0.5)
            continue

        # ── Step 5: upsert hourly OHLC to DB ──────────────────────────────────
        upsert_ohlc(hourly_rows)

        # ── Step 6: load last 90 days of ohlc_hourly from DB for this symbol ──
        ohlc_rows = get_ohlc(sid, LOOKBACK_DAYS)
        if not ohlc_rows:
            print('SKIP — no ohlc rows in DB after upsert')
            failed.append(ticker)
            time.sleep(0.5)
            continue

        # ── Steps 7-9: compute ATR means, apply k-ratios, upsert vbh_stats ───
        stat_rows = compute_vbh_rows(sid, ohlc_rows)
        upsert_vbh_stats(stat_rows)

        # Quick validation: count RTH hours (9–16 ET) with valid data
        rth_counts = [
            row['sample_count'] for row in stat_rows
            if row['model'] == 'AGG' and 9 <= row['hour_et'] < 17
        ]
        min_rth = min(rth_counts) if rth_counts else 0
        max_rth = max(rth_counts) if rth_counts else 0

        print(f'OK  ({len(candles)} 30min candles → {len(hourly_rows)} hourly bars, '
              f'{min_rth}–{max_rth} obs/RTH hour)')
        ok.append(ticker)

        # ── Rate limit: 0.5s between symbols ──────────────────────────────────
        time.sleep(0.5)

    print(f'\n{"─" * 55}')
    print(f'Updated : {ok}')
    if failed:
        print(f'Failed  : {failed}')
    print('\nDone — vbh_stats updated in Supabase.')
    print('The live app will pick up the new levels on next restart or stats refresh.')


if __name__ == '__main__':
    main()
