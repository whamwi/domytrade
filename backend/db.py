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


# ── 1-min bars (market futures only) ─────────────────────────────────────────

def upsert_1min(rows: list[dict]) -> None:
    """Upsert 1-min bars for /ES, /NQ, /YM, /RTY — used for VWAP/POC."""
    if not rows:
        return
    get_db().table('ohlc_1min').upsert(rows, on_conflict='symbol_id,bar_time').execute()


def get_1min_range(symbol_id: int, days: int = 3) -> list[dict]:
    """Return 1-min bars for the last N calendar days — for sparklines and analysis."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = (get_db().table('ohlc_1min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', symbol_id)
           .gte('bar_time', cutoff)
           .order('bar_time')
           .execute())
    return res.data


def get_1min_today(symbol_id: int) -> list[dict]:
    """Return today's 1-min bars from midnight ET — for VWAP/POC computation."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now_et   = datetime.now(ZoneInfo('America/New_York'))
    midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff   = midnight.astimezone(timezone.utc).isoformat()
    res = (get_db().table('ohlc_1min')
           .select('open,high,low,close,volume')
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
    """Fetch daily bars for multiple tickers in ONE query. Returns {ticker: [bars]}."""
    if not tickers:
        return {}
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    res = (get_db().table('ticker_candles_daily')
           .select('ticker,bar_date,open,high,low,close,volume')
           .in_('ticker', tickers)
           .gte('bar_date', cutoff)
           .order('bar_date')
           .execute())
    result: dict[str, list[dict]] = {t: [] for t in tickers}
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


# ── VBH Signals ───────────────────────────────────────────────────────────────

def insert_signals(rows: list[dict]) -> None:
    """Insert a batch of signals (historical record)."""
    if not rows:
        return
    get_db().table('vbh_signals').insert(rows).execute()


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
