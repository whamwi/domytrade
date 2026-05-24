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


# ── VBH Signals ───────────────────────────────────────────────────────────────

def insert_signals(rows: list[dict]) -> None:
    """Insert a batch of signals (historical record)."""
    if not rows:
        return
    get_db().table('vbh_signals').insert(rows).execute()
