#!/usr/bin/env python3
"""
daily_update.py — Self-bootstrapping candle pull + swing scan.

Data sources (in priority order):
  1. Schwab price history API  — daily, weekly, monthly natively
  2. yfinance                  — fallback for tickers Schwab rejects

On first run (empty DB): full backfill for every timeframe.
On subsequent runs:       incremental pull (recent bars only).
Always ends with a full swing rescan persisted to swing_scan_results.

Scheduled at 4:30 PM ET (20:30 UTC) Mon–Fri on Railway.

Usage:
    python3 daily_update.py
    python3 daily_update.py --dry-run    # candles only, skip scan
"""
import sys, os, time, argparse, warnings
from datetime import datetime, timezone, date

import pandas as pd
import requests

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))

from db import get_db
from schwab_client import PRICE_HISTORY_URL, _headers, get_quotes

# ── Thresholds — below these, do a full historical backfill ───────────────────
MIN_DAILY_BARS   = 210    # need 5y for reliable squeeze
MIN_WEEKLY_BARS  = 42
MIN_MONTHLY_BARS = 42

SLEEP_S = 0.15            # polite delay between Schwab calls

# ── Schwab backfill vs incremental params ─────────────────────────────────────
SCHWAB_PARAMS = {
    #            (period_type, period, freq_type,  freq)
    'daily':   { 'backfill': ('year',  5,  'daily',   1),
                 'update':   ('month', 1,  'daily',   1) },
    'weekly':  { 'backfill': ('year',  5,  'weekly',  1),
                 'update':   ('month', 3,  'weekly',  1) },
    'monthly': { 'backfill': ('year',  20, 'monthly', 1),
                 'update':   ('year',  2,  'monthly', 1) },
}

TABLE = {
    'daily':   'ticker_candles_daily',
    'weekly':  'ticker_candles_weekly',
    'monthly': 'ticker_candles_monthly',
}

BAR_COUNT_RPC = {
    'daily':   'get_daily_bar_counts',
    'weekly':  'get_weekly_bar_counts',
    'monthly': 'get_monthly_bar_counts',
}

MIN_BARS = {
    'daily':   MIN_DAILY_BARS,
    'weekly':  MIN_WEEKLY_BARS,
    'monthly': MIN_MONTHLY_BARS,
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


# ── Universe ──────────────────────────────────────────────────────────────────

def get_universe(db) -> list[str]:
    resp = db.table('ticker_universe').select('ticker').execute()
    return sorted(r['ticker'] for r in resp.data)


def bar_counts(db, tf: str) -> dict[str, int]:
    resp = db.rpc(BAR_COUNT_RPC[tf], {}).execute()
    return {r['ticker']: r['bar_count'] for r in resp.data}


# ── Schwab fetch ──────────────────────────────────────────────────────────────

def fetch_schwab(symbol: str, period_type: str, period: int,
                 freq_type: str, freq: int) -> pd.DataFrame:
    params = {
        'symbol'               : symbol,
        'periodType'           : period_type,
        'period'               : period,
        'frequencyType'        : freq_type,
        'frequency'            : freq,
        'needExtendedHoursData': 'false',
    }
    r = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if r.status_code == 401:
        r = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if not r.ok:
        return pd.DataFrame()
    candles = r.json().get('candles', [])
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df['bar_date'] = pd.to_datetime(df['datetime'], unit='ms', utc=True) \
                       .dt.tz_convert('America/New_York').dt.date
    return df[['bar_date', 'open', 'high', 'low', 'close', 'volume']]


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert(db, ticker: str, df: pd.DataFrame, tf: str) -> int:
    if df.empty:
        return 0
    rows = df.to_dict('records')
    for r in rows:
        r['ticker']   = ticker
        r['bar_date'] = str(r['bar_date'])
        for k in ('open', 'high', 'low', 'close'):
            v = r[k]
            r[k] = float(v.item() if hasattr(v, 'item') else v)
        v = r['volume']
        r['volume'] = int(round(float(v.item() if hasattr(v, 'item') else v)))
    rows = [r for r in rows if None not in r.values()]
    if not rows:
        return 0
    db.table(TABLE[tf]).upsert(rows, on_conflict='ticker,bar_date').execute()
    return len(rows)


# ── Update one timeframe ──────────────────────────────────────────────────────

def update_tf(db, tickers: list[str], tf: str):
    counts   = bar_counts(db, tf)
    min_bars = MIN_BARS[tf]

    thin = [t for t in tickers if counts.get(t, 0) < min_bars]
    fat  = [t for t in tickers if counts.get(t, 0) >= min_bars]

    log(f"{tf.upper()}: {len(thin)} backfill  |  {len(fat)} incremental")

    total = ok = failed = 0
    for mode, lst in [('backfill', thin), ('update', fat)]:
        if not lst:
            continue
        pt, p, ft, f = SCHWAB_PARAMS[tf][mode]
        for ticker in lst:
            total += 1
            df = fetch_schwab(ticker, pt, p, ft, f)
            n  = upsert(db, ticker, df, tf)
            if n:
                ok += 1
                if ok <= 3 or ok % 100 == 0:
                    print(f"  {ticker:8s} {n} bars", flush=True)
            else:
                failed += 1
                print(f"  {ticker:8s} NO DATA (Schwab)", flush=True)
            time.sleep(SLEEP_S)

    log(f"{tf.upper()} done: {ok}/{total} tickers  ({failed} failed)")


# ── Today's bar injection (quotes-based, handles Schwab history lag) ──────────

def _inject_today_bar(db, tickers: list[str]) -> None:
    """Upsert today's OHLCV bar using the RTH close from Schwab quotes.

    Schwab's price history API lags ~1 business day.  The quotes endpoint
    does not have a direct 'RTH close' field, but it provides:
      - lastPrice        : most recent trade (AH price during after-hours)
      - postMarketChange : lastPrice - RTH_close

    So: RTH_close = lastPrice - postMarketChange
    When there is no AH trading, postMarketChange=0 and lastPrice IS the RTH close.
    When AH trading is active, this correctly strips out the AH move.

    NOTE: 'mark' (bid-ask mid) tracks AH prices and is NOT reliable as the RTH close.
    """
    from zoneinfo import ZoneInfo
    ET = ZoneInfo('America/New_York')
    today_str = datetime.now(ET).date().isoformat()
    stocks = [t for t in tickers if not t.startswith('/')]
    log(f"Injecting today's bar ({today_str}) using RTH close (last - postMarketChange) for {len(stocks)} symbols")

    CHUNK = 100
    ok = 0
    for i in range(0, len(stocks), CHUNK):
        chunk = stocks[i:i+CHUNK]
        quotes = get_quotes(chunk)
        rows = []
        for ticker in chunk:
            q = quotes.get(ticker, {})
            last        = float(q.get('last') or 0)
            post_change = float(q.get('post_market_change') or 0)
            close       = last - post_change   # RTH close = last - AH move
            if not close:
                continue
            rows.append({
                'ticker':   ticker,
                'bar_date': today_str,
                'open':     float(q.get('open') or close),
                'high':     float(q.get('high') or close),
                'low':      float(q.get('low')  or close),
                'close':    float(close),
                'volume':   int(q.get('volume') or 0),
            })
        if rows:
            db.table('ticker_candles_daily').upsert(rows, on_conflict='ticker,bar_date').execute()
            ok += len(rows)
        time.sleep(0.2)

    log(f"Today's bar injected: {ok}/{len(stocks)} symbols")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Candles only — skip scan')
    parser.add_argument('--tf', choices=['daily', 'weekly', 'monthly'],
                        help='Update only one timeframe')
    args = parser.parse_args()

    t0      = time.time()
    db      = get_db()
    tickers = get_universe(db)
    log(f"daily_update starting — {len(tickers)} tickers")

    tfs = [args.tf] if args.tf else ['daily', 'weekly', 'monthly']
    for tf in tfs:
        update_tf(db, tickers, tf)

    # Schwab price history lags ~1 day — inject today's OHLCV from live quotes
    # so the scanner always has the current session's close.
    if 'daily' in tfs and not args.tf:
        _inject_today_bar(db, tickers)

    if args.dry_run:
        log("--dry-run: skipping scan")
    else:
        log("Running swing scan…")
        from scanner import scan_swing
        results = scan_swing(persist=True)
        fired   = [r for r in results
                   if r.get('d_sq_state') == 'FIRED'
                   or r.get('w_sq_state') == 'FIRED'
                   or r.get('m_sq_state') == 'FIRED']
        log(f"Scan complete: {len(results)} tickers  |  {len(fired)} with ≥1 TF fired")

    log(f"Total time: {int(time.time()-t0)}s")


if __name__ == '__main__':
    main()
