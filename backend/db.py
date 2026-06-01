"""
Supabase client — read/write market data and signals.
Uses service role key (bypasses RLS) for all backend writes.
"""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client | None = None


def get_db() -> Client:
    global _client
    if _client is None:
        url = os.environ['SUPABASE_URL']
        key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
        _client = create_client(url, key)
    return _client


# ── Symbols ────────────────────────────────────────────────────────────────────

def get_active_symbols() -> list[dict]:
    """Return all active symbols: [{id, ticker, schwab_symbol, asset_type}]"""
    res = get_db().table('symbols').select('*').eq('is_active', True).order('id').execute()
    return res.data


# ── OHLC ───────────────────────────────────────────────────────────────────────

def upsert_ohlc(rows: list[dict]) -> None:
    """Upsert hourly bars. rows = [{symbol_id, bar_time, hour_et, open, high, low, close, volume}]"""
    if not rows:
        return
    get_db().table('ohlc_hourly').upsert(rows, on_conflict='symbol_id,bar_time').execute()


def get_ohlc(symbol_id: int, lookback_days: int) -> list[dict]:
    """Fetch hourly bars for a symbol over the last N days."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    res = (get_db().table('ohlc_hourly')
           .select('bar_time,hour_et,open,high,low,close,volume')
           .eq('symbol_id', symbol_id)
           .gte('bar_time', cutoff)
           .order('bar_time')
           .execute())
    return res.data


# ── VBH Stats ─────────────────────────────────────────────────────────────────

def upsert_vbh_stats(rows: list[dict]) -> None:
    """Upsert computed L1/L2/L3/L4 stats."""
    if not rows:
        return
    get_db().table('vbh_stats').upsert(rows, on_conflict='symbol_id,model,hour_et').execute()


def get_vbh_stats(symbol_id: int) -> list[dict]:
    """Return all stats rows for a symbol (both AGG and CON, all hours)."""
    res = (get_db().table('vbh_stats')
           .select('model,hour_et,l1,l2,l3,l4,sample_count')
           .eq('symbol_id', symbol_id)
           .execute())
    return res.data


# ── 30-min bars (all symbols — VBH source) ───────────────────────────────────

def upsert_30min(rows: list[dict]) -> None:
    """Upsert 30-min OHLCV bars.
    rows = [{symbol_id, bar_time, hour_et, minute_et, open, high, low, close, volume}]
    """
    if not rows:
        return
    get_db().table('ohlc_30min').upsert(rows, on_conflict='symbol_id,bar_time').execute()


def get_30min(symbol_id: int, lookback_days: int) -> list[dict]:
    """Fetch 30-min bars for a symbol over the last N days (for VBH stat computation).

    Paginates in 1 000-row pages to work around Supabase PostgREST's default
    max-rows cap (which silently truncates even large explicit .limit() calls).
    365d × 48 bars/day ≈ 17 500 bars per equity symbol.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    PAGE   = 1_000
    offset = 0
    all_rows: list[dict] = []

    while True:
        res = (get_db().table('ohlc_30min')
               .select('bar_time,hour_et,minute_et,open,high,low,close,volume')
               .eq('symbol_id', symbol_id)
               .gte('bar_time', cutoff)
               .order('bar_time')
               .range(offset, offset + PAGE - 1)
               .execute())
        batch = res.data
        all_rows.extend(batch)
        if len(batch) < PAGE:
            break                # last page — done
        offset += PAGE

    return all_rows


def get_last_30min_bar_times(symbol_ids: list[int]) -> dict[int, str | None]:
    """Return {symbol_id: max_bar_time_isoformat} for incremental 30-min fetching."""
    if not symbol_ids:
        return {}
    result = {sid: None for sid in symbol_ids}
    for sid in symbol_ids:
        res = (get_db().table('ohlc_30min')
               .select('bar_time')
               .eq('symbol_id', sid)
               .order('bar_time', desc=True)
               .limit(1)
               .execute())
        if res.data:
            result[sid] = res.data[0]['bar_time']
    return result


# ── 1-min bars (market futures only) ─────────────────────────────────────────

def upsert_1min(rows: list[dict]) -> None:
    """Upsert 1-min bars for /ES, /NQ, /YM, /RTY — used for VWAP/POC."""
    if not rows:
        return
    get_db().table('ohlc_1min').upsert(rows, on_conflict='symbol_id,bar_time').execute()


def get_1min_range(symbol_id: int, days: int = 3) -> list[dict]:
    """Return 1-min bars for the last N calendar days — for sparklines and analysis.

    Fetches newest-first with a generous limit (5 000 rows covers 3+ days of
    24-hour futures data at 1-min resolution), then re-sorts ascending so
    callers always receive chronological order with up-to-date bars.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = (get_db().table('ohlc_1min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', symbol_id)
           .gte('bar_time', cutoff)
           .order('bar_time', desc=True)
           .limit(5000)
           .execute())
    return list(reversed(res.data))


def get_1min_today(symbol_id: int) -> list[dict]:
    """Return today's 1-min bars from midnight ET — for VWAP/POC computation."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now_et   = datetime.now(ZoneInfo('America/New_York'))
    midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff   = midnight.astimezone(timezone.utc).isoformat()
    res = (get_db().table('ohlc_1min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', symbol_id)
           .gte('bar_time', cutoff)
           .order('bar_time')
           .execute())
    return res.data


# ── Ticker daily candles (holdings — 90-day Fib source) ───────────────────────

def upsert_daily_candles(rows: list[dict]) -> None:
    """Upsert daily bars. rows = [{ticker, bar_date, open, high, low, close, volume}]"""
    if not rows:
        return
    get_db().table('ticker_candles_daily').upsert(rows, on_conflict='ticker,bar_date').execute()


def get_daily_candles_db(ticker: str, days: int = 90) -> list[dict]:
    """Return daily bars for the last N days for a given ticker."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    res = (get_db().table('ticker_candles_daily')
           .select('bar_date,open,high,low,close,volume')
           .eq('ticker', ticker)
           .gte('bar_date', cutoff)
           .order('bar_date')
           .execute())
    return res.data


def get_daily_candles_batch(tickers: list[str], days: int = 90) -> dict[str, list[dict]]:
    """Fetch daily bars for multiple tickers. Returns {ticker: [bars]}.

    Paginates in 1000-row pages to bypass Supabase's server-side row cap.
    200 tickers × 90 bars ≈ 18 000 rows — requires ~18 pages without pagination.
    """
    if not tickers:
        return {}
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    result: dict[str, list[dict]] = {t: [] for t in tickers}
    PAGE = 1000
    offset = 0
    while True:
        res = (get_db().table('ticker_candles_daily')
               .select('ticker,bar_date,open,high,low,close,volume')
               .in_('ticker', tickers)
               .gte('bar_date', cutoff)
               .order('bar_date')
               .range(offset, offset + PAGE - 1)
               .execute())
        for row in res.data:
            t = row['ticker']
            if t in result:
                result[t].append({
                    'bar_date': row['bar_date'],
                    'open'    : row['open'],
                    'high'    : row['high'],
                    'low'     : row['low'],
                    'close'   : row['close'],
                    'volume'  : row['volume'],
                })
        if len(res.data) < PAGE:
            break          # last page
        offset += PAGE
    return result


def get_last_daily_bar_dates(tickers: list[str]) -> dict[str, str]:
    """Return {ticker: max_bar_date} for incremental daily updates."""
    if not tickers:
        return {}
    res = (get_db().table('ticker_candles_daily')
           .select('ticker,bar_date')
           .in_('ticker', tickers)
           .order('bar_date', desc=True)
           .execute())
    result: dict[str, str] = {}
    for row in res.data:
        t = row['ticker']
        if t not in result:
            result[t] = row['bar_date']
    return result


def delete_old_daily_candles(keep_days: int = 120) -> int:
    """Delete ticker_candles_daily rows older than keep_days."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).date().isoformat()
    res = (get_db().table('ticker_candles_daily')
           .delete()
           .lt('bar_date', cutoff)
           .execute())
    return len(res.data) if res.data else 0


# ── Ticker-keyed 1-min candles (holdings + all stocks, no FK) ─────────────────

def upsert_ticker_candles(rows: list[dict]) -> None:
    """Upsert 1-min bars keyed by ticker string. rows = [{ticker, bar_time, open, high, low, close, volume}]"""
    if not rows:
        return
    get_db().table('ticker_candles_1min').upsert(rows, on_conflict='ticker,bar_time').execute()


def get_ticker_candles(ticker: str, days: int = 3) -> list[dict]:
    """Return 1-min bars for the last N days for a given ticker."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = (get_db().table('ticker_candles_1min')
           .select('bar_time,open,high,low,close,volume')
           .eq('ticker', ticker)
           .gte('bar_time', cutoff)
           .order('bar_time')
           .execute())
    return res.data


def get_etf_holding_tickers() -> list[str]:
    """Return all unique tickers across all cached ETF holdings (stored in app_cache)."""
    res = get_db().table('app_cache') \
        .select('key,value') \
        .like('key', 'etf_holdings_%') \
        .execute()
    tickers: set[str] = set()
    for row in res.data:
        holdings = (row.get('value') or {}).get('holdings', [])
        for h in holdings:
            t = h.get('ticker', '').strip()
            if t:
                tickers.add(t)
    return list(tickers)


def get_last_bar_times(tickers: list[str]) -> dict[str, str]:
    """Return {ticker: max_bar_time} for the given tickers — used for incremental updates."""
    if not tickers:
        return {}
    res = (get_db().table('ticker_candles_1min')
           .select('ticker,bar_time')
           .in_('ticker', tickers)
           .order('bar_time', desc=True)
           .execute())
    # Keep only the most recent bar_time per ticker
    result: dict[str, str] = {}
    for row in res.data:
        t = row['ticker']
        if t not in result:
            result[t] = row['bar_time']
    return result


def delete_old_ticker_candles(keep_days: int = 4) -> int:
    """Delete ticker_candles_1min rows older than keep_days. Returns deleted count."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    res = (get_db().table('ticker_candles_1min')
           .delete()
           .lt('bar_time', cutoff)
           .execute())
    return len(res.data) if res.data else 0


# ── 15-min aggregation (daily compaction job) ─────────────────────────────────

def aggregate_1min_to_15min(cutoff_days: int = 2) -> dict:
    """
    Aggregate ohlc_1min bars older than `cutoff_days` into 15-min OHLCV bars.

    Steps:
      1. Fetch all 1-min rows older than the cutoff (in pages to avoid row cap).
      2. Group into 15-min buckets keyed by (symbol_id, bucket_bar_time).
         bucket_bar_time = floor(minute / 15) * 15, seconds zeroed.
      3. Upsert into ohlc_15min — on_conflict='symbol_id,bar_time' prevents duplicates
         (safe to re-run; existing 15-min bars are simply overwritten with the same data).
      4. Delete the processed 1-min rows (only after successful upsert).

    Returns {'aggregated': N_buckets, 'deleted': N_1min_rows}.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()

    # ── Step 1: page through old 1-min rows ───────────────────────────────────
    rows_1min: list[dict] = []
    PAGE = 1000
    offset = 0
    while True:
        res = (get_db().table('ohlc_1min')
               .select('symbol_id,bar_time,open,high,low,close,volume')
               .lt('bar_time', cutoff)
               .order('bar_time')
               .range(offset, offset + PAGE - 1)
               .execute())
        rows_1min.extend(res.data)
        if len(res.data) < PAGE:
            break
        offset += PAGE

    if not rows_1min:
        return {'aggregated': 0, 'deleted': 0}

    # ── Step 2: group into 15-min buckets ─────────────────────────────────────
    # We need a stable ordering within each bucket to get correct open/close.
    # Sort by bar_time so the first row = open, last row = close.
    rows_1min.sort(key=lambda r: r['bar_time'])

    buckets: dict[tuple, dict] = {}
    for r in rows_1min:
        dt = datetime.fromisoformat(r['bar_time'])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minute_floor = (dt.minute // 15) * 15
        bucket_dt    = dt.replace(minute=minute_floor, second=0, microsecond=0)
        key          = (r['symbol_id'], bucket_dt.isoformat())

        if key not in buckets:
            buckets[key] = {
                'open'  : r['open'],
                'high'  : r['high'],
                'low'   : r['low'],
                'close' : r['close'],
                'volume': r.get('volume') or 0,
            }
        else:
            b = buckets[key]
            if r['high'] > b['high']:
                b['high'] = r['high']
            if r['low'] < b['low']:
                b['low'] = r['low']
            b['close']   = r['close']          # always last close in bucket
            b['volume'] += (r.get('volume') or 0)

    # ── Step 3: upsert 15-min rows (idempotent) ───────────────────────────────
    rows_15min = [
        {
            'symbol_id': k[0],
            'bar_time' : k[1],
            'open'     : v['open'],
            'high'     : v['high'],
            'low'      : v['low'],
            'close'    : v['close'],
            'volume'   : v['volume'],
        }
        for k, v in buckets.items()
    ]

    # Upsert in chunks to stay well under Supabase payload limits (~500 rows/request)
    CHUNK = 500
    for i in range(0, len(rows_15min), CHUNK):
        (get_db().table('ohlc_15min')
         .upsert(rows_15min[i:i + CHUNK], on_conflict='symbol_id,bar_time')
         .execute())

    # ── Step 4: delete aggregated 1-min rows ──────────────────────────────────
    # Supabase Python client doesn't support .limit() on delete queries.
    # Delete per symbol_id — this is safe because we only delete rows
    # within the same cutoff window we used to fetch rows_1min.
    symbol_ids = list({r['symbol_id'] for r in rows_1min})
    deleted = 0
    for sid in symbol_ids:
        res = (get_db().table('ohlc_1min')
               .delete()
               .eq('symbol_id', sid)
               .lt('bar_time', cutoff)
               .execute())
        deleted += len(res.data) if res.data else 0

    return {'aggregated': len(rows_15min), 'deleted': deleted}


# ── VBH Signals ───────────────────────────────────────────────────────────────

def insert_signals(rows: list[dict]) -> None:
    """Insert a batch of signals (historical record)."""
    if not rows:
        return
    get_db().table('vbh_signals').insert(rows).execute()


# ── Entry Log (forward-testing record of every ENTRY transition) ───────────────

def insert_entry_log(rows: list[dict]) -> None:
    """Persist ENTRY alert rows to entry_log for forward-testing analysis."""
    if not rows:
        return
    get_db().table('entry_log').insert(rows).execute()


def get_entry_log(limit: int = 200, model: str = 'all', side: str = 'all') -> list[dict]:
    """Return the most recent entry_log rows, newest first.

    model: 'all' | 'AGG' | 'CON' | 'WIDE' | 'CR'
    side:  'all' | 'LONG' | 'SHORT'
    """
    q = get_db().table('entry_log').select('*').order('fired_at', desc=True)
    if model != 'all':
        q = q.eq('model', model.upper())
    if side != 'all':
        q = q.eq('side', side.upper())
    res = q.limit(limit).execute()
    return res.data or []


def clear_entry_log() -> int:
    """Delete all entry_log rows. Returns the number of rows deleted."""
    res = get_db().table('entry_log').delete().gte('id', 0).execute()
    return len(res.data) if res.data else 0


# ── App Cache (key/value store for persisting computed values) ─────────────────

def cache_get(key: str) -> dict | None:
    """Read a cached value by key. Returns None if not found."""
    res = get_db().table('app_cache').select('value').eq('key', key).execute()
    if res.data:
        return res.data[0]['value']
    return None


# ── ETF Holdings (stored in app_cache, refreshed daily from Yahoo Finance) ────

def get_etf_holdings(etf_ticker: str) -> list[dict]:
    """Return cached top-10 holdings for an ETF. Empty list if not yet fetched."""
    data = cache_get(f'etf_holdings_{etf_ticker.upper()}')
    return data.get('holdings', []) if data else []


def set_etf_holdings(etf_ticker: str, holdings: list[dict]) -> None:
    """Cache top-10 holdings for an ETF."""
    from datetime import datetime, timezone
    cache_set(f'etf_holdings_{etf_ticker.upper()}', {
        'holdings'  : holdings,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    })


def cache_set(key: str, value: dict) -> None:
    """Upsert a cached value."""
    from datetime import datetime, timezone
    get_db().table('app_cache').upsert(
        {'key': key, 'value': value, 'updated_at': datetime.now(timezone.utc).isoformat()},
        on_conflict='key'
    ).execute()


# ── VBH incremental helpers ───────────────────────────────────────────────────

def get_last_ohlc_bar_times(symbol_ids: list[int]) -> dict[int, str | None]:
    """Return {symbol_id: max_bar_time_isoformat} for incremental candle fetching.
    Returns None for symbols with no data yet."""
    if not symbol_ids:
        return {}
    result = {sid: None for sid in symbol_ids}
    # Query max bar_time per symbol_id using separate queries (Supabase doesn't support GROUP BY directly)
    for sid in symbol_ids:
        res = (get_db().table('ohlc_hourly')
               .select('bar_time')
               .eq('symbol_id', sid)
               .order('bar_time', desc=True)
               .limit(1)
               .execute())
        if res.data:
            result[sid] = res.data[0]['bar_time']
    return result


def load_vbh_stats_from_db() -> dict[str, dict[str, list[tuple]]]:
    """Load all vbh_stats rows from DB and return as:
    {ticker: {'AGG': [24 tuples (l1,l2,l3,l4)], 'CON': [24 tuples]}}
    Hours with no data get (0.0, 0.0, 0.0, 0.0).
    """
    # Get all symbols for ticker lookup
    syms = get_db().table('symbols').select('id,ticker').execute().data
    id_to_ticker = {s['id']: s['ticker'] for s in syms}

    # Fetch all vbh_stats rows — paginate to avoid the 1000-row default cap
    PAGE = 1000
    all_rows = []
    offset = 0
    while True:
        res = (get_db().table('vbh_stats')
               .select('symbol_id,model,hour_et,l1,l2,l3,l4')
               .range(offset, offset + PAGE - 1)
               .execute())
        all_rows.extend(res.data)
        if len(res.data) < PAGE:
            break
        offset += PAGE

    result: dict[str, dict[str, list]] = {}
    for row in all_rows:
        ticker = id_to_ticker.get(row['symbol_id'])
        if not ticker:
            continue
        model = row['model']
        if ticker not in result:
            result[ticker] = {
                'AGG' : [(0.0, 0.0, 0.0, 0.0)] * 24,
                'CON' : [(0.0, 0.0, 0.0, 0.0)] * 24,
                'WIDE': [(0.0, 0.0, 0.0, 0.0)] * 24,
            }
        h = row['hour_et']
        result[ticker][model][h] = (
            float(row['l1'] or 0),
            float(row['l2'] or 0),
            float(row['l3'] or 0),
            float(row['l4'] or 0),
        )
    return result
