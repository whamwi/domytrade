#!/usr/bin/env python3
"""
One-time backfill: fetch missing 1-min bars from Schwab for May 22-25, 2026
and aggregate them into the ohlc_15min table.

Run from the backend directory:
    python3 backfill_15min.py
"""
import os, sys, time, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Load local .env (Supabase + Schwab credentials)
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Add backend dir to path so we can import project modules
sys.path.insert(0, os.path.dirname(__file__))

from db import get_db, upsert_1min, aggregate_1min_to_15min
from schwab_client import front_month_code, PRICE_HISTORY_URL, _headers

# ── Futures to backfill ────────────────────────────────────────────────────────
FUTURES_TICKERS = ['/ES', '/NQ', '/YM', '/RTY', '/GC']

# ── Date windows for missing data (UTC) ───────────────────────────────────────
# May 22 23:45 → May 25 23:59 UTC covers the 3 missing trading days
WINDOWS = [
    (datetime(2026, 5, 22, 0, 0,  tzinfo=timezone.utc),
     datetime(2026, 5, 22, 23, 59, tzinfo=timezone.utc)),
    (datetime(2026, 5, 23, 0, 0,  tzinfo=timezone.utc),
     datetime(2026, 5, 23, 23, 59, tzinfo=timezone.utc)),
    (datetime(2026, 5, 24, 0, 0,  tzinfo=timezone.utc),
     datetime(2026, 5, 24, 23, 59, tzinfo=timezone.utc)),
    (datetime(2026, 5, 25, 0, 0,  tzinfo=timezone.utc),
     datetime(2026, 5, 25, 23, 59, tzinfo=timezone.utc)),
]

def get_symbol_map() -> dict[str, int]:
    """Return {ticker: symbol_id} for the futures we care about."""
    db = get_db()
    res = db.table('symbols').select('id,ticker').in_('ticker', FUTURES_TICKERS).execute()
    return {row['ticker']: row['id'] for row in res.data}

def fetch_candles(schwab_symbol: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch 1-min bars from Schwab for the given UTC window."""
    params = {
        'symbol'              : schwab_symbol,
        'frequencyType'       : 'minute',
        'frequency'           : 1,
        'startDate'           : int(start.timestamp() * 1000),
        'endDate'             : int(end.timestamp()   * 1000),
        'needExtendedHoursData': 'true',
    }
    try:
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=30)
        if not resp.ok:
            print(f'  Schwab error {resp.status_code}: {resp.text[:200]}')
            return []
        data = resp.json()
        return data.get('candles', [])
    except Exception as e:
        print(f'  fetch error: {e}')
        return []

def candles_to_rows(symbol_id: int, candles: list[dict]) -> list[dict]:
    """Convert Schwab candle dicts to ohlc_1min row dicts."""
    rows = []
    for c in candles:
        try:
            bar_time = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).isoformat()
            rows.append({
                'symbol_id': symbol_id,
                'bar_time' : bar_time,
                'open'     : float(c['open']),
                'high'     : float(c['high']),
                'low'      : float(c['low']),
                'close'    : float(c['close']),
                'volume'   : int(c.get('volume', 0)),
            })
        except (KeyError, TypeError, ValueError):
            pass
    return rows

def main():
    print('── domytrade 15-min backfill ──────────────────────────────────────────')
    sym_map = get_symbol_map()
    print(f'Symbol IDs: {sym_map}')

    total_1min = 0

    for ticker in FUTURES_TICKERS:
        symbol_id = sym_map.get(ticker)
        if symbol_id is None:
            print(f'⚠  {ticker} not found in DB, skipping')
            continue

        # Resolve to the front-month contract code (same for May 22-25 as today)
        schwab_symbol = front_month_code(ticker)
        print(f'\n{ticker} → {schwab_symbol} (id={symbol_id})')

        for start, end in WINDOWS:
            candles = fetch_candles(schwab_symbol, start, end)
            if not candles:
                print(f'  {start.date()} → no data from Schwab')
                continue
            rows = candles_to_rows(symbol_id, candles)
            if rows:
                upsert_1min(rows)
                total_1min += len(rows)
                print(f'  {start.date()}: inserted {len(rows)} bars')
            time.sleep(0.3)   # gentle rate limiting

    print(f'\nTotal 1-min bars inserted: {total_1min}')
    print('Running aggregate_1min_to_15min(cutoff_days=2)…')

    result = aggregate_1min_to_15min(cutoff_days=2)
    print(f'  aggregated: {result["aggregated"]} 15-min buckets')
    print(f'  deleted:    {result["deleted"]} 1-min rows')
    print('\n✓ Backfill complete.')

if __name__ == '__main__':
    main()
