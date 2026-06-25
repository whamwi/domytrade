"""
domytrade.app — FastAPI backend
Serves live VBH signals. Persists OHLC history and signals to Supabase.
"""
import asyncio, logging, os, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests as _req
import numpy as np
import pandas as pd
from fastapi import FastAPI, Query, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from schwab_client import (get_quotes, get_candles, get_daily_candles,
                           get_current_hour_ohlc, get_session_bars,
                           front_month_code, next_contract_month,
                           set_token_refresh_callback, _token_cache as _schwab_token_cache,
                           get_accounts, get_positions, get_orders, get_transactions,
                           place_futures_order, close_futures_position, cancel_order,
                           place_equity_order, close_equity_position,
                           front_month_code, _trader_get)
import vbh_engine
from vbh_engine import compute_stats, compute_stats_con, compute_stats_wide, make_signal
from squeeze import calc_squeeze_5min, squeeze_confirms_signal
import market_profile_rules as _mp_rules
from db import (get_active_symbols, upsert_ohlc, get_ohlc,
                upsert_vbh_stats, get_vbh_stats, insert_signals,
                upsert_1min, get_1min_today, get_1min_range,
                upsert_ticker_candles, get_ticker_candles, get_etf_holding_tickers,
                get_last_bar_times, delete_old_ticker_candles,
                upsert_daily_candles, get_daily_candles_db, get_daily_candles_batch,
                get_last_daily_bar_dates, delete_old_daily_candles,
                get_etf_holdings, set_etf_holdings,
                aggregate_1min_to_15min,
                insert_entry_log, get_entry_log, clear_entry_log,
                get_prev_rth_hours)

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)s  %(message)s')
log = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')

AGG_DAYS = 30
CON_DAYS = 90
STATS_REFRESH_HOURS = 24
SIGNAL_REFRESH_SECS = 30
HL_REFRESH_SECS     = 10   # fast H/L accumulator — closes gap with TOS tick-by-tick
PRICE_REFRESH_SECS  =  5   # fast price-only push via WebSocket


# Sector / industry ETF tickers — used by frontend for "Sectors" filter
SECTOR_TICKERS = {
    'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE',
    'SMH','HACK','SKYY','TAN','JETS','OIH','IYT','EEM','SOCL','KCE','XLG','XRT','OEF',
}

# Fast lookup set for strip ETFs — used in refresh_signals() for RTH open updates
STRIP_TICKERS = {'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE'}

# Ordered list for the Industries strip — 11 SPDR sector ETFs only
# S&P 500 sector weights — update manually each quarter from SPDR fact sheets.
# April 2026 GICS allocations. Order matches ThinkScript AddLabel order.
STRIP_ETFS = [
    {'ticker': 'XLK',  'name': 'Tech',    'weight': 27.0},
    {'ticker': 'XLV',  'name': 'Health',  'weight': 14.0},
    {'ticker': 'XLF',  'name': 'Fin',     'weight': 13.0},
    {'ticker': 'XLY',  'name': 'C/Disc',  'weight': 10.6},
    {'ticker': 'XLC',  'name': 'Comms',   'weight': 10.8},
    {'ticker': 'XLI',  'name': 'Ind',     'weight':  8.6},
    {'ticker': 'XLP',  'name': 'Stpls',   'weight':  5.9},
    {'ticker': 'XLE',  'name': 'Energy',  'weight':  3.2},
    {'ticker': 'XLB',  'name': 'Matls',   'weight':  2.5},
    {'ticker': 'XLU',  'name': 'Utils',   'weight':  2.4},
    {'ticker': 'XLRE', 'name': 'R/E',     'weight':  2.3},
]

# Key sectors to watch per futures instrument — ordered by relevance
SECTORS_FOR = {
    '/NQ' : [('XLK', 'Tech'), ('XLC', 'Comms'), ('XLY', 'C.Disc')],
    '/MNQ': [('XLK', 'Tech'), ('XLC', 'Comms'), ('XLY', 'C.Disc')],
    '/YM' : [('XLV', 'Health'), ('XLF', 'Fin'), ('XLI', 'Ind'), ('XLY', 'C.Disc')],
    '/MYM': [('XLV', 'Health'), ('XLF', 'Fin'), ('XLI', 'Ind'), ('XLY', 'C.Disc')],
    '/ES' : [('XLK', 'Tech'), ('XLF', 'Fin'), ('XLV', 'Health')],
    '/MES': [('XLK', 'Tech'), ('XLF', 'Fin'), ('XLV', 'Health')],
    '/RTY': [('XLF', 'Fin'), ('XLV', 'Health'), ('XLI', 'Ind')],
    '/M2K': [('XLF', 'Fin'), ('XLV', 'Health'), ('XLI', 'Ind')],
}

# MAG10 custom composite index — price-weighted basket of mega-cap tech
# Formula: Σ (price / divisor * weight) — % from RTH open
MAG10_COMPONENTS = [
    {'ticker': 'AAPL',  'div': 2.7, 'weight': 0.15},
    {'ticker': 'AMZN',  'div': 2.5, 'weight': 0.10},
    {'ticker': 'AVGO',  'div': 4.0, 'weight': 0.06},
    {'ticker': 'GOOGL', 'div': 3.8, 'weight': 0.16},
    {'ticker': 'META',  'div': 6.0, 'weight': 0.07},
    {'ticker': 'MSFT',  'div': 4.0, 'weight': 0.10},
    {'ticker': 'AMD',   'div': 3.0, 'weight': 0.05},
    {'ticker': 'NVDA',  'div': 2.0, 'weight': 0.16},
    {'ticker': 'TSLA',  'div': 3.8, 'weight': 0.08},
    {'ticker': 'TSM',   'div': 4.0, 'weight': 0.07},
]

# ── Global markets data (Asian indices + FX risk-on/off) ───────────────────────
ASIAN_INDICES = [
    {'symbol': '^N225',     'name': 'Nikkei',    'region': 'JP'},
    {'symbol': '^HSI',      'name': 'Hang Seng', 'region': 'HK'},
    {'symbol': '000001.SS', 'name': 'Shanghai',  'region': 'CN'},
    {'symbol': '^AXJO',     'name': 'ASX 200',   'region': 'AU'},
]

# FX pairs: yfinance symbol kept for reference; schwab_symbol is what we quote from Schwab
FX_PAIRS = [
    {'schwab_symbol': 'USD/JPY', 'name': 'USD/JPY', 'risk': 'off'},  # safe-haven
    {'schwab_symbol': 'EUR/USD', 'name': 'EUR/USD', 'risk': 'on'},
    {'schwab_symbol': 'GBP/USD', 'name': 'GBP/USD', 'risk': 'on'},
]

_GLOBAL_MARKETS_CACHE: dict = {}
_GLOBAL_MARKETS_TTL = 900   # 15 minutes (FX refreshes on this cadence)

_LEVELS_CACHE: dict = {}    # {symbol: {'data': dict, 'ts': datetime}}
_LEVELS_CACHE_TTL = 30      # seconds — levels stable enough to cache

_VPOCS_CACHE: dict = {}     # {symbol: {'data': dict, 'ts': datetime}}
_VPOCS_CACHE_TTL = 120      # seconds — naked VPOCs change slowly

# Stock fundamental profiles — keyed by ticker, refreshed daily at 6 AM ET via yfinance.
# Also attempted to persist to Supabase stock_profiles table (graceful if table absent).
# DDL to create the table in Supabase SQL editor:
#   CREATE TABLE IF NOT EXISTS public.stock_profiles (
#       ticker TEXT PRIMARY KEY, company_name TEXT, sector TEXT, industry TEXT,
#       market_cap BIGINT, pe_trailing NUMERIC(10,2), pe_forward NUMERIC(10,2),
#       eps_trailing NUMERIC(10,4), week_52_high NUMERIC(12,4), week_52_low NUMERIC(12,4),
#       analyst_rating TEXT, analyst_count INTEGER, target_price NUMERIC(12,4),
#       beta NUMERIC(8,4), dividend_yield NUMERIC(8,4), description TEXT,
#       refreshed_at TIMESTAMPTZ DEFAULT now()
#   );
_STOCK_PROFILES: dict = {}   # {ticker: profile_dict}


# ── In-memory cache (rebuilt from DB on startup) ───────────────────────────────
state = {
    'symbols'          : [],   # [{id, ticker, schwab_symbol, asset_type}]
    'stats_agg'        : {},   # {symbol_id: {hour: (L1,L2,L3,L4)}}
    'stats_con'        : {},
    'stats_wide'       : {},   # WIDE (extra-conservative) model — shift=4.0σ
    'prev_close'       : {},   # {symbol_id: float}  — last RTH close from candles
    'market_bias'      : {},   # {symbol_id: {bias, pts, rth_open, prev_close}}
    'last_price'       : {},   # {symbol_id: float}  — latest price (live quote or prev_close fallback)
    'net_change'       : {},   # {symbol_id: float}  — Schwab net_change (vs CME settlement / prev close)
    'rth_open'         : {},   # {symbol_id: float}  — today's RTH 9:30 open (from 1-min DB bar)
    'prev_settle'      : {},   # {symbol_id: float}  — prior CME settlement (last - net_change), persisted
    'ib'               : {},   # {symbol_id: {'high': float, 'low': float, 'complete': bool}}
    'volatility'       : {'vix': None},   # $VIX — Fear Index
    'ytd'              : {},   # {ticker: float}  — YTD % for all SECTOR_TICKERS + SPY
    'mag10_last'        : {},    # {ticker: float}  — live last price
    'mag10_prev_close'  : {},    # {ticker: float}  — prev close = last - net_change
    'daily_bias'        : {},    # {symbol_id: 'LONG'|'SHORT'}  — RTH open vs prev_settle
    'prev_signal_state' : {},    # {sid_model: 'NEAR'|'ENTRY'}  — for ENTRY transition detection
    'hourly_high'       : {},    # {symbol_id: float}  — running max of last_price since current ET hour started
    'hourly_low'        : {},    # {symbol_id: float}  — running min of last_price since current ET hour started
    'hourly_hour'       : {},    # {symbol_id: int}    — ET hour when above accumulators were last reset
    'strip_session_date': None,  # date — set by refresh_strip_opens when a real RTH candle is found today
    'strip_prev_close'  : {},    # {symbol_id: float}  — prior RTH close for STRIP_ETFS, refreshed every 5 min from daily candles (independent of 24h stats cycle)
    'signals'           : [],
    'squeeze'           : {},   # {symbol_id: sq_result} — 5-min SqueezePRO for MARKET_TICKERS
    'last_stats_update': None,
    'last_signal_update': None,
    'status'           : 'starting',
    'active_contracts'  : {},   # {ticker: active_contract} e.g. {'/GC': '/GCQ26'}
    'last_loop_error'   : None,  # most recent background loop exception string
    'market_profile'    : {},   # {symbol: full market profile result} — updated by /api/market-profile calls
    'token_state'       : 'unknown',  # 'loaded_from_db' | 'using_env_var' | 'load_failed'
    '1min_today'        : {},   # {symbol_id: [bars]} — in-memory cache populated by refresh_all_1min()
    'cr'                : {},   # {symbol_id: {high, low, mid, entry_long, entry_short, complete}}
    'cr_breached'       : {},   # {symbol_id: 'LONG'|'SHORT'|None} — which CR extreme was first breached
    'cr_date'           : None, # date string — CR is reset each new trading day
    'cr_sticky'         : {},   # {f"{sid}_CR": int} — remaining cycles to keep NEAR visible (max 4)
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prev_rth_close(candles: list[dict]) -> float:
    """Return the close of the most recent COMPLETED RTH bar (weekday 9:30–16:00 ET).

    Excludes today's bars so the result is always a prior session's close, never the
    current intraday bar. This prevents holiday CME bars (which fall in the RTH time
    window on a weekday) from being mistaken for the prior RTH settlement price.
    """
    if not candles:
        return 0.0
    today = datetime.now(ET).date()
    rth = [
        (c['datetime'], c['close'])
        for c in candles
        if (lambda dt: (
            dt.date() < today and                          # prior session only
            dt.weekday() < 5 and
            9 * 60 + 30 <= dt.hour * 60 + dt.minute <= 16 * 60
        ))(datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET))
    ]
    return float(sorted(rth)[-1][1]) if rth else 0.0


NEUTRAL_BAND = 4.0   # points — within ±4 pts of prev close = NEUTRAL

def _rth_bias(candles: list[dict]) -> dict:
    """
    Compare most recent RTH session open (9:30 ET) vs previous RTH session close (16:00 ET).
    Returns {'bias': BULL|BEAR|NEUTRAL, 'pts': float, 'rth_open': float, 'prev_close': float}
    Off-hours: stays frozen at last completed session's result.
    """
    if not candles:
        return {'bias': 'NEUTRAL', 'pts': 0.0, 'rth_open': 0.0, 'prev_close': 0.0}

    # Group RTH bars by date {date: {'open': first_bar_open, 'close': last_bar_close}}
    sessions: dict = {}
    for c in sorted(candles, key=lambda x: x['datetime']):
        dt  = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        if dt.weekday() >= 5:
            continue
        t   = dt.hour * 60 + dt.minute
        day = dt.date()
        if 9 * 60 + 30 <= t <= 16 * 60:
            if day not in sessions:
                sessions[day] = {'open': c['open'], 'close': c['close']}
            else:
                sessions[day]['close'] = c['close']   # keep last bar

    if len(sessions) < 2:
        return {'bias': 'NEUTRAL', 'pts': 0.0, 'rth_open': 0.0, 'prev_close': 0.0}

    dates      = sorted(sessions.keys())
    rth_open   = sessions[dates[-1]]['open']
    prev_close = sessions[dates[-2]]['close']
    pts        = round(rth_open - prev_close, 2)

    if abs(pts) <= NEUTRAL_BAND:
        bias = 'NEUTRAL'
    elif pts > 0:
        bias = 'BULL'
    else:
        bias = 'BEAR'

    return {'bias': bias, 'pts': pts, 'rth_open': rth_open, 'prev_close': prev_close}


# ── Clearing Range (CR) ───────────────────────────────────────────────────────
CR_BREACH_TICKS = 2   # ticks beyond CR high/low to confirm a breach

def _compute_cr(min_bars: list, tick_size: float) -> dict | None:
    """Compute the 30-min Clearing Range (9:30–10:00 AM ET) from 1-min Schwab bars.

    Returns dict with high/low/mid/entry_long/entry_short or None if IB not yet complete.
    Bars use Schwab's b['datetime'] (Unix ms) format.
    """
    now_et     = datetime.now(ET)
    et_minute  = now_et.hour * 60 + now_et.minute
    if et_minute < 10 * 60:          # IB window not done yet
        return None
    if now_et.weekday() >= 5:        # weekend — no CR
        return None

    ib_start = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    ib_end   = now_et.replace(hour=10, minute=0,  second=0, microsecond=0)
    ib_start_ms = int(ib_start.astimezone(timezone.utc).timestamp() * 1000)
    ib_end_ms   = int(ib_end.astimezone(timezone.utc).timestamp() * 1000)

    ib_bars = [b for b in min_bars if ib_start_ms <= b.get('datetime', 0) < ib_end_ms]
    if not ib_bars:
        return None

    cr_high = max(b['high'] for b in ib_bars)
    cr_low  = min(b['low']  for b in ib_bars)
    ts      = tick_size or 0.01
    entry_long  = round((cr_high + CR_BREACH_TICKS * ts) / ts) * ts
    entry_short = round((cr_low  - CR_BREACH_TICKS * ts) / ts) * ts
    mid         = round((entry_long + entry_short) / 2, 6)

    return {
        'high'       : cr_high,
        'low'        : cr_low,
        'mid'        : mid,
        'entry_long' : round(entry_long, 6),
        'entry_short': round(entry_short, 6),
        'complete'   : True,
    }


# ── VWAP / POC ────────────────────────────────────────────────────────────────

MARKET_TICK = {'/ES': 0.25, '/NQ': 0.25, '/YM': 1.0, '/RTY': 0.10}

def _compute_vwap_poc(bars: list[dict], tick_size: float) -> dict:
    """Compute session VWAP and Point of Control from 1-min bars."""
    if not bars:
        return {'vwap': None, 'poc': None}
    total_tpv = sum(((b['high'] + b['low'] + b['close']) / 3) * b['volume'] for b in bars)
    total_vol  = sum(b['volume'] for b in bars)
    vwap = round(total_tpv / total_vol, 2) if total_vol > 0 else None

    # Volume profile — distribute each bar's volume across its price ticks
    tick_vol: dict[float, float] = {}
    for bar in bars:
        lo = round(round(bar['low']  / tick_size) * tick_size, 6)
        hi = round(round(bar['high'] / tick_size) * tick_size, 6)
        n  = max(1, round((hi - lo) / tick_size) + 1)
        vpt = bar['volume'] / n
        t = lo
        for _ in range(n):
            k = round(t, 6)
            tick_vol[k] = tick_vol.get(k, 0) + vpt
            t = round(t + tick_size, 6)

    poc = round(max(tick_vol, key=tick_vol.get), 2) if tick_vol else None
    return {'vwap': vwap, 'poc': poc}


def _compute_tpo_value_area(bars: list[dict], tick: float, pct: float = 0.70, period_min: int = 30) -> dict:
    """Dalton TPO (Time Price Opportunity) Market Profile — original method.

    Groups 1-min bars into `period_min`-minute periods (A=first 30 min, B=next, …).
    Each period 'touches' every price tick between the period's low and high (1 TPO each).
    POC  = price touched by the most periods.
    Value Area = greedy expansion from POC until ≥pct of total TPO count captured.

    Unlike volume profile, this is computed *exactly* from 1-min bars — no intrabar
    volume distribution assumptions needed.

    Returns {'poc', 'vah', 'val', 'periods'}.
    """
    from collections import defaultdict
    _empty = {'poc': None, 'vah': None, 'val': None, 'periods': 0}
    if not bars:
        return _empty

    # Group 1-min bars into 30-min period buckets by ET wall-clock time
    period_bars: dict = defaultdict(list)
    for b in bars:
        dt    = datetime.fromtimestamp(b['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        t_min = dt.hour * 60 + dt.minute
        key   = (dt.date(), t_min // period_min)
        period_bars[key].append(b)

    if not period_bars:
        return _empty

    # Build TPO map — each period contributes exactly 1 count per tick it touched
    tpo_map: dict[float, int] = defaultdict(int)
    for pbars in period_bars.values():
        period_hi = max(b['high'] for b in pbars)
        period_lo = min(b['low']  for b in pbars)
        lo_t = round(round(period_lo / tick) * tick, 6)
        hi_t = round(round(period_hi / tick) * tick, 6)
        p = lo_t
        while p <= hi_t + tick * 0.001:   # small epsilon avoids float rounding gaps
            tpo_map[round(p, 6)] += 1
            p = round(p + tick, 6)

    if not tpo_map:
        return _empty

    total_tpos = sum(tpo_map.values())
    if not total_tpos:
        return _empty
    target  = total_tpos * pct

    poc     = max(tpo_map, key=tpo_map.get)
    prices  = sorted(tpo_map.keys())
    poc_idx = prices.index(poc)

    # Greedy expansion from POC (same logic as volume profile)
    va_set  = {poc}
    va_tpos = tpo_map[poc]
    lo_idx  = poc_idx
    hi_idx  = poc_idx

    while va_tpos < target:
        can_up   = hi_idx + 1 < len(prices)
        can_down = lo_idx - 1 >= 0
        if not can_up and not can_down:
            break
        up_cnt = tpo_map[prices[hi_idx + 1]] if can_up   else -1
        dn_cnt = tpo_map[prices[lo_idx - 1]] if can_down else -1
        if up_cnt >= dn_cnt:
            hi_idx += 1
            va_set.add(prices[hi_idx])
            va_tpos += up_cnt
        else:
            lo_idx -= 1
            va_set.add(prices[lo_idx])
            va_tpos += dn_cnt

    return {
        'poc':     round(poc, 2),
        'vah':     round(max(va_set), 2),
        'val':     round(min(va_set), 2),
        'periods': len(period_bars),
    }


def _compute_value_area(bars: list[dict], tick: float, pct: float = 0.70) -> dict:
    """Value Area: price range containing `pct` (default 70%) of session volume.
    Returns {'poc': float, 'vah': float, 'val': float}.
    Classic TPO/Market Profile algorithm starting from POC, expanding greedy."""
    from collections import defaultdict
    _empty = {'poc': None, 'vah': None, 'val': None}
    if not bars:
        return _empty
    vol_map: dict[float, float] = defaultdict(float)
    for b in bars:
        hi, lo, vol = b['high'], b['low'], b.get('volume', 0)
        if not vol:
            continue
        lo_t = round(round(lo / tick) * tick, 6)
        hi_t = round(round(hi / tick) * tick, 6)
        n    = max(1, round((hi_t - lo_t) / tick) + 1)
        vpt  = vol / n
        p    = lo_t
        for _ in range(n):
            vol_map[round(p, 6)] += vpt
            p = round(p + tick, 6)
    if not vol_map:
        return _empty
    total_vol = sum(vol_map.values())
    if not total_vol:
        return _empty
    target    = total_vol * pct
    poc       = max(vol_map, key=vol_map.get)
    prices    = sorted(vol_map.keys())
    poc_idx   = prices.index(poc)
    # Expand outward from POC
    va_set    = {poc}
    va_vol    = vol_map[poc]
    lo_idx    = poc_idx
    hi_idx    = poc_idx
    while va_vol < target:
        can_up   = hi_idx + 1 < len(prices)
        can_down = lo_idx - 1 >= 0
        if not can_up and not can_down:
            break
        up_vol   = vol_map[prices[hi_idx + 1]] if can_up   else -1
        dn_vol   = vol_map[prices[lo_idx - 1]] if can_down else -1
        if up_vol >= dn_vol:
            hi_idx += 1
            va_set.add(prices[hi_idx])
            va_vol += up_vol
        else:
            lo_idx -= 1
            va_set.add(prices[lo_idx])
            va_vol += dn_vol
    return {
        'poc': round(poc, 2),
        'vah': round(max(va_set), 2),
        'val': round(min(va_set), 2),
    }


# ── EMA helper ────────────────────────────────────────────────────────────────


# ── OHLC helpers ──────────────────────────────────────────────────────────────

def _candles_to_ticker_rows(ticker: str, candles: list[dict]) -> list[dict]:
    """Convert 1-min Schwab candles to ticker_candles_1min rows (no symbol_id FK)."""
    rows = []
    for c in candles:
        dt = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
        rows.append({
            'ticker'  : ticker,
            'bar_time': dt.isoformat(),
            'open'    : float(c['open']),
            'high'    : float(c['high']),
            'low'     : float(c['low']),
            'close'   : float(c['close']),
            'volume'  : int(c['volume']) if c['volume'] else 0,
        })
    return rows


def _candles_to_1min_rows(symbol_id: int, candles: list[dict]) -> list[dict]:
    """Convert 1-min Schwab candles to ohlc_1min rows."""
    rows = []
    for c in candles:
        dt = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
        rows.append({
            'symbol_id': symbol_id,
            'bar_time' : dt.isoformat(),
            'open'     : float(c['open']),
            'high'     : float(c['high']),
            'low'      : float(c['low']),
            'close'    : float(c['close']),
            'volume'   : int(c['volume']) if c['volume'] else 0,
        })
    return rows


def _candles_to_rows(symbol_id: int, candles: list[dict]) -> list[dict]:
    """Convert Schwab candles to ohlc_hourly rows."""
    if not candles:
        return []
    df = pd.DataFrame(candles)
    df['dt'] = pd.to_datetime(df['datetime'], unit='ms', utc=True)
    df = df.set_index('dt')[['open', 'high', 'low', 'close', 'volume']]
    hourly = df.resample('1h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    ).dropna(subset=['open'])
    hourly.index = hourly.index.tz_convert(ET)
    hourly['hour_et'] = hourly.index.hour
    rows = []
    for ts, row in hourly.iterrows():
        rows.append({
            'symbol_id': symbol_id,
            'bar_time' : ts.tz_convert('UTC').isoformat(),
            'hour_et'  : int(row['hour_et']),
            'open'     : float(row['open']),
            'high'     : float(row['high']),
            'low'      : float(row['low']),
            'close'    : float(row['close']),
            'volume'   : int(row['volume']) if row['volume'] else 0,
        })
    return rows


def _ohlc_rows_to_candles(rows: list[dict]) -> list[dict]:
    """Convert DB ohlc rows back to Schwab-style candles for vbh_engine."""
    result = []
    for r in rows:
        dt = datetime.fromisoformat(r['bar_time'])
        result.append({
            'datetime': int(dt.timestamp() * 1000),
            'open'    : r['open'],
            'high'    : r['high'],
            'low'     : r['low'],
            'close'   : r['close'],
            'volume'  : r['volume'],
        })
    return result


# ── Stats computation ─────────────────────────────────────────────────────────

async def compute_all_stats():
    log.info('Computing VBH stats (AGG=%dd, CON=%dd)…', AGG_DAYS, CON_DAYS)
    symbols = state['symbols']

    for sym in symbols:
        sid   = sym['id']
        tick  = sym['ticker']
        api   = sym['schwab_symbol']
        try:
            # Fetch 90d candles from Schwab and persist to DB
            # Futures price history requires a specific contract month symbol (e.g. /ESM26)
            # Continuous symbols like /ES:XCME are rejected by Schwab's price history API
            candle_sym = _active_contract(tick) if tick.startswith('/') else api
            log.info('  %-8s  fetching candles as %s', tick, candle_sym)
            con_candles = await asyncio.to_thread(get_candles, candle_sym, CON_DAYS)
            rows = _candles_to_rows(sid, con_candles)
            if rows:
                upsert_ohlc(rows)

            state['prev_close'][sid]   = _prev_rth_close(con_candles)
            state['market_bias'][sid]  = _rth_bias(con_candles)
            if con_candles:
                state['last_price'][sid] = round(con_candles[-1]['close'], 4)

            # Stocks: skip dynamic Schwab-candle computation; load from DB instead
            # (ThinkScript-imported rows have lookback_days=-1; uncovered stocks → {})
            if not tick.startswith('/'):
                state['stats_agg'][sid]  = compute_stats([], api)
                state['stats_con'][sid]  = compute_stats_con([], api)
                state['stats_wide'][sid] = compute_stats_wide([], api)
                await asyncio.sleep(0.4)
                continue

            # Compute stats directly from fresh candles (no DB round-trip needed)
            # AGG = last 30 days subset of the 90d fetch
            cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=AGG_DAYS)).timestamp() * 1000)
            agg_candles = [c for c in con_candles if c['datetime'] >= cutoff_ms]

            state['stats_agg'][sid]    = compute_stats(agg_candles, api)
            state['stats_con'][sid]    = compute_stats_con(con_candles, api)
            state['stats_wide'][sid]   = compute_stats_wide(con_candles, api)

            # Persist stats to DB
            stat_rows = []
            for h in range(24):
                for model, stats_dict in [('AGG',  state['stats_agg'][sid]),
                                           ('CON',  state['stats_con'][sid]),
                                           ('WIDE', state['stats_wide'][sid])]:
                    l1, l2, l3, l4 = stats_dict.get(h, (0, 0, 0, 0))
                    stat_rows.append({
                        'symbol_id'    : sid,
                        'model'        : model,
                        'hour_et'      : h,
                        'l1'           : l1,
                        'l2'           : l2,
                        'l3'           : l3,
                        'l4'           : l4,
                        'sample_count' : None,
                        'lookback_days': AGG_DAYS if model == 'AGG' else CON_DAYS,
                        'computed_at'  : datetime.now(ET).isoformat(),
                    })
            upsert_vbh_stats(stat_rows)

            log.info('  %-8s  bars=%d', tick, len(con_candles))
            await asyncio.sleep(0.4)

        except Exception as e:
            log.warning('  %-8s  ERROR: %s', tick, e)

    # Backfill 1-min bars for ALL futures → DB
    # Market futures (/ES /NQ /YM /RTY): 10 days — needed for VWAP/IB history.
    # All other futures: 3 days — covers overnight + 2 prior sessions for hourly OHLC.
    log.info('Backfilling 1-min bars for all futures…')
    for sym in symbols:
        tick = sym['ticker']
        if not tick.startswith('/'):
            continue
        sid      = sym['id']
        lb_days  = 10 if tick in MARKET_TICKERS else 3
        try:
            bars = await asyncio.to_thread(get_candles, _active_contract(tick), lb_days, 1)
            rows = _candles_to_1min_rows(sid, bars)
            if rows:
                upsert_1min(rows)
            log.info('  %-8s  1min=%d bars saved (%dd)', tick, len(rows), lb_days)
            await asyncio.sleep(0.3)
        except Exception as e:
            log.warning('  %-8s  1min ERROR: %s', tick, e)

    # Backfill 3 days of 1-min bars for all stocks & ETFs → DB
    log.info('Backfilling 3d 1-min bars for stocks/ETFs…')
    for sym in symbols:
        tick = sym['ticker']
        if tick.startswith('/'):
            continue   # futures handled above
        sid = sym['id']
        api = sym['schwab_symbol']
        try:
            bars = await asyncio.to_thread(get_candles, api, 3, 1)
            rows = _candles_to_1min_rows(sid, bars)
            if rows:
                upsert_1min(rows)
            log.info('  %-8s  1min=%d bars saved', tick, len(rows))
            await asyncio.sleep(0.3)
        except Exception as e:
            log.warning('  %-8s  1min ERROR: %s', tick, e)

    # Backfill 3 days of 1-min bars for all ETF holdings → ticker_candles_1min
    log.info('Backfilling 3d 1-min bars for ETF holdings…')
    try:
        holding_tickers = await asyncio.to_thread(get_etf_holding_tickers)
        # Also include watchlist stocks/ETFs so /api/candles is consistent
        watchlist_tickers = [s['schwab_symbol'] for s in symbols if not s['ticker'].startswith('/')]
        all_tickers = list(set(holding_tickers + watchlist_tickers))
        log.info('  %d unique holding tickers to backfill', len(all_tickers))
        sem = asyncio.Semaphore(4)  # 4 concurrent Schwab requests

        async def _backfill_ticker(tkr: str):
            async with sem:
                try:
                    # Normalize BRK/B → BRK%2FB handled by schwab client
                    bars = await asyncio.to_thread(get_candles, tkr, 3, 1)
                    rows = _candles_to_ticker_rows(tkr, bars)
                    if rows:
                        await asyncio.to_thread(upsert_ticker_candles, rows)
                    await asyncio.sleep(0.25)
                except Exception as e:
                    log.warning('  holdings 1min %s: %s', tkr, e)

        await asyncio.gather(*[_backfill_ticker(t) for t in all_tickers])
        log.info('  Holdings 1min backfill done.')
    except Exception as e:
        log.warning('Holdings 1min backfill error: %s', e)

    state['last_stats_update'] = datetime.now(ET).isoformat()
    log.info('Stats ready.')


# ── Incremental 1-min candle updater for holdings ─────────────────────────────
# Rotates through all tickers in batches of 20 per 60s cycle.
# Full rotation completes every ~10 min. Only runs during RTH + pre-market.
_candle_batch_idx = 0
_candle_tickers:  list[str] = []   # populated on first call
_CANDLE_BATCH_SIZE = 20

async def refresh_holding_candles() -> None:
    """Incrementally append new 1-min bars for a rotating batch of holding tickers."""
    global _candle_batch_idx, _candle_tickers

    now_et  = datetime.now(ET)
    t_min   = now_et.hour * 60 + now_et.minute
    weekday = now_et.weekday()

    # Only run Mon–Fri between 4:00 AM and 5:00 PM ET (pre-market → 1h after close)
    if weekday >= 5 or not (4 * 60 <= t_min <= 17 * 60):
        return

    # Build ticker list on first call or after daily stats refresh
    if not _candle_tickers:
        try:
            holding_tickers  = await asyncio.to_thread(get_etf_holding_tickers)
            watchlist_stocks = [s['schwab_symbol'] for s in state.get('symbols', [])
                                if not s['ticker'].startswith('/')]
            _candle_tickers  = sorted(set(holding_tickers + watchlist_stocks))
            log.info('Candle updater: %d tickers registered', len(_candle_tickers))
        except Exception as e:
            log.warning('Candle updater ticker load failed: %s', e)
            return

    if not _candle_tickers:
        return

    # Slice this cycle's batch
    total   = len(_candle_tickers)
    start   = _candle_batch_idx % total
    batch   = (_candle_tickers + _candle_tickers)[start:start + _CANDLE_BATCH_SIZE]
    _candle_batch_idx = (start + _CANDLE_BATCH_SIZE) % total

    # Get last stored bar per ticker in this batch
    last_times = await asyncio.to_thread(get_last_bar_times, batch)

    sem = asyncio.Semaphore(5)

    async def update_one(ticker: str) -> int:
        async with sem:
            try:
                raw = await asyncio.to_thread(get_candles, ticker, 1, 1)  # 1 day, 1-min
                if not raw:
                    return 0
                last_stored = last_times.get(ticker)
                if last_stored:
                    cutoff_ms = int(datetime.fromisoformat(last_stored).timestamp() * 1000)
                    raw = [c for c in raw if c['datetime'] > cutoff_ms]
                rows = _candles_to_ticker_rows(ticker, raw)
                if rows:
                    await asyncio.to_thread(upsert_ticker_candles, rows)
                await asyncio.sleep(0.2)
                return len(rows)
            except Exception as e:
                log.debug('candle update %s: %s', ticker, e)
                return 0

    counts  = await asyncio.gather(*[update_one(t) for t in batch])
    new_bars = sum(counts)
    if new_bars:
        log.info('Candle update: +%d bars across %d tickers (batch %d/%d)',
                 new_bars, len(batch), start // _CANDLE_BATCH_SIZE + 1,
                 -(-total // _CANDLE_BATCH_SIZE))


async def refresh_signals():
    symbols = state['symbols']

    # ── RTH check (used to gate openPrice updates for strip ETFs) ───────────
    _now_et  = datetime.now(ET)
    _et_min  = _now_et.hour * 60 + _now_et.minute
    _is_rth  = _now_et.weekday() < 5 and (9 * 60 + 30) <= _et_min < 16 * 60

    # All futures use the specific front-month contract symbol for quotes (e.g. /ESM26).
    # Schwab echoes back the same key we send, so quote_key maps it back to schwab_symbol.
    # Continuous symbols (e.g. /GC:XCME) would have Schwab respond with the front-month
    # key (/GCM26), breaking the round-trip lookup → last=0 → no signal generated.
    # Equities/ETFs use schwab_symbol as-is.
    def _quote_sym(s):
        tick = s['ticker']
        return _active_contract(tick) if tick.startswith('/') else s['schwab_symbol']

    quote_syms = [_quote_sym(s) for s in symbols]
    # Map back: quote_symbol → schwab_symbol so we can look up the right key
    quote_key = {_quote_sym(s): s['schwab_symbol'] for s in symbols}

    try:
        quotes_raw = await asyncio.to_thread(get_quotes, quote_syms)
    except Exception as e:
        state['last_loop_error'] = f'get_quotes: {type(e).__name__}: {e}'
        log.warning('Quote fetch error: %s', e)
        return

    # Normalize: keyed by schwab_symbol (continuous) for the rest of the code
    quotes = {quote_key.get(qs, qs): v for qs, v in quotes_raw.items()}

    # Fetch $VIX (Fear Index)
    try:
        vol_quotes = await asyncio.to_thread(get_quotes, ['$VIX'])
        vix_last   = vol_quotes.get('$VIX', {}).get('last') or None
        state['volatility'] = {
            'vix': round(vix_last, 2) if vix_last else state['volatility'].get('vix'),
        }
    except Exception as e:
        log.warning('VIX quote error: %s', e)

    signal_hour = datetime.now(ET).replace(minute=0, second=0, microsecond=0)
    rows = []

    for sym in symbols:
        sid  = sym['id']
        tick = sym['ticker']
        api  = sym['schwab_symbol']

        q          = quotes.get(api, {})
        last       = q.get('last', 0)
        prev_close = state['prev_close'].get(sid, 0)   # last RTH close from candles

        # Always record the best available price.
        # Priority: live quote → last candle close (set by compute_all_stats) → RTH prev_close
        candle_last = state['last_price'].get(sid)   # seeded from absolute last candle
        display_price = last if last else (candle_last or prev_close)
        if display_price:
            state['last_price'][sid] = round(display_price, 4)

        # Hourly H/L accumulator is updated AFTER building hour_bars (below) so
        # that on hour change / cold start it can be seeded from DB bar data
        # rather than just the live price.  See the block after ohlc = None.

        # During RTH, keep rth_open current for strip ETFs from the live quote's openPrice.
        # Schwab openPrice = official 9:30 ET open once the regular session is underway.
        # We skip pre-market / after-hours to avoid using a pre-market first-trade as the open.
        if _is_rth and tick in STRIP_TICKERS:
            q_open = q.get('open', 0)
            if q_open:
                state['rth_open'][sid] = round(q_open, 4)

        # Store net_change from Schwab — matches TOS "Change" column.
        # Futures: change vs previous CME settlement. Equities: change vs prev close.
        net_chg_raw = q.get('net_change', 0)
        if net_chg_raw:
            state['net_change'][sid] = round(net_chg_raw, 4)

        # Market bias for the 4 equity index futures.
        # Use Schwab's net_change (last - prev CME settlement) as the reliable
        # previous-close reference. Bias = session open vs prev settlement.
        # During off-hours openPrice is 0 → fall back to net_change direction.
        if sym['ticker'] in MARKET_TICKERS:
            net_chg  = q.get('net_change', 0)
            q_open   = q.get('open', 0)
            q_last   = last
            # prev_settle is always accurate for futures (official CME settlement).
            # Use None (not 0) when net_chg/last is missing so gap guard stays correct.
            # Persist across cycles so gap stays visible when quote goes stale (off-hours).
            if net_chg and q_last:
                prev_settle = round(q_last - net_chg, 2)
                state['prev_settle'][sid] = prev_settle
            else:
                prev_settle = state['prev_settle'].get(sid)
            # Always show the LIVE running change (current price vs prev CME settlement).
            # This matches the TOS "Change" column and stays current throughout the session.
            pts = round(net_chg, 2) if net_chg else 0.0
            if abs(pts) <= NEUTRAL_BAND:
                mbias = 'NEUTRAL'
            elif pts > 0:
                mbias = 'BULL'
            else:
                mbias = 'BEAR'
            # 1-min bars are now kept current by refresh_all_1min() which runs
            # before every refresh_signals() cycle — no need to fetch again here.
            # Just read back what's already in DB for VWAP/POC and IB computation.
            fresh = []
            today_rows = []   # always in scope — avoids NameError in gap loop below
            try:
                today_rows = await asyncio.to_thread(get_1min_today, sid)
                vp = _compute_vwap_poc(today_rows, MARKET_TICK.get(tick, 0.25))
                # Convert DB rows back to candle-like dicts for IB computation below
                fresh = [{'datetime': int(datetime.fromisoformat(r['bar_time']).timestamp() * 1000),
                           'high': r['high'], 'low': r['low']} for r in today_rows]
            except Exception:
                vp = {'vwap': None, 'poc': None}

            # Initial Balance: high/low of first 30 min of RTH (9:30–10:00 ET)
            try:
                ib_s = 9 * 60 + 30
                ib_e = 10 * 60
                ib_bars = []
                for c in fresh:   # fresh = DB rows converted above
                    dt_c = datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET)
                    t_min_c = dt_c.hour * 60 + dt_c.minute
                    if ib_s <= t_min_c < ib_e:
                        ib_bars.append(c)
                now_et_ib = datetime.now(ET)
                ib_complete = (now_et_ib.weekday() < 5 and
                               now_et_ib.hour * 60 + now_et_ib.minute >= ib_e)
                if ib_bars:
                    state['ib'][sid] = {
                        'high'    : max(c['high'] for c in ib_bars),
                        'low'     : min(c['low']  for c in ib_bars),
                        'complete': ib_complete,
                    }
                elif sid not in state['ib']:
                    state['ib'][sid] = {'high': None, 'low': None, 'complete': False}
            except Exception:
                pass

            # Gap = RTH 9:30 open vs prior CME settlement.
            # prev_settle = last - net_change = exact CME settlement (most accurate for futures).
            # rth_open: find the first 1-min bar at exactly 9:30 ET from today_rows.
            # q.get('open') from Schwab is the CME SESSION open (Sunday 6 PM), NOT 9:30 — don't use it.
            rth_open_for_gap = None
            try:
                for r in today_rows:   # already ordered bar_time ASC
                    dt_r = datetime.fromisoformat(r['bar_time']).astimezone(ET)
                    if dt_r.hour == 9 and dt_r.minute == 30:
                        rth_open_for_gap = r['open']
                        break
            except Exception:
                pass
            # Persist for off-hours display (so gap stays visible after 4 PM)
            if rth_open_for_gap:
                state['rth_open'][sid] = round(rth_open_for_gap, 4)
            else:
                rth_open_for_gap = state['rth_open'].get(sid)
            gap = round(rth_open_for_gap - prev_settle, 2) if (rth_open_for_gap and prev_settle) else None
            state['market_bias'][sid] = {
                'bias': mbias, 'pts': pts,
                'rth_open': rth_open_for_gap, 'prev_close': prev_settle,
                'vwap': vp['vwap'], 'poc': vp['poc'],
                'gap': gap,
            }

            # ── 5-min SqueezePRO — computed once per signal cycle for market futures ──
            # Uses 3 days of 1-min bars already in DB → ~828 five-min bars after agg.
            # Result cached in state['squeeze'][sid] and attached to signal rows below.
            try:
                _sq_rows = await asyncio.to_thread(get_1min_range, sid, 3)
                # Run numpy/pandas squeeze compute in thread pool — keeps event loop free
                state['squeeze'][sid] = await asyncio.to_thread(calc_squeeze_5min, _sq_rows)
            except Exception as _sq_e:
                log.warning('Squeeze error %s: %s', tick, _sq_e)

        if not last:
            continue

        # Build current-hour OHLC from 1-min bars in DB.
        # refresh_all_1min() runs before every refresh_signals() cycle and populates
        # 1-min bars for ALL futures — so hour_bars will be non-empty for every symbol.
        # Fold last_price into high/low to match TOS live-tick behaviour (the current
        # developing 1-min bar isn't closed yet so its high/low isn't in DB yet).
        # Fallback: if no bars yet (first run, cold start) anchor to last_price only.
        ohlc     = None
        cur_hour = datetime.now(ET).hour   # fallback if try block throws
        try:
            now_et_h   = datetime.now(ET)
            cur_hour   = now_et_h.hour
            hour_floor = now_et_h.replace(minute=0, second=0, microsecond=0)
            # Read from in-memory cache populated by refresh_all_1min() — avoids
            # 153 sequential DB round-trips per cycle that previously caused timeouts.
            # Falls back to empty list if cache hasn't been populated yet (first cycle).
            min_bars      = state['1min_today'].get(sid, [])
            # Schwab candles use b['datetime'] (Unix ms) — NOT b['bar_time'].
            # Comparing in ms avoids ISO-parse overhead and the KeyError that
            # previously caused ohlc to silently fall back to the accumulator only.
            # For stocks, h9 floor is 9:30am (RTH open), not 9:00am.
            # get_session_bars uses needExtendedHoursData=true, so 1min_today
            # includes 9:00-9:30am pre-market bars for stocks.  Without this
            # adjustment, pre-market highs inflate h_high and trigger false
            # LONG ENTRY signals (entry > current price) at the RTH open.
            # Futures are unaffected — they genuinely trade 9:00-9:30am.
            if not tick.startswith('/') and cur_hour == 9:
                rth_open = hour_floor.replace(minute=30)
                hour_floor_ms = int(rth_open.astimezone(timezone.utc).timestamp() * 1000)
            else:
                hour_floor_ms = int(hour_floor.astimezone(timezone.utc).timestamp() * 1000)
            hour_bars     = [b for b in min_bars if b.get('datetime', 0) >= hour_floor_ms]
            if hour_bars:
                ohlc = {
                    'open'  : hour_bars[0]['open'],
                    'high'  : max(b['high']   for b in hour_bars),
                    'low'   : min(b['low']    for b in hour_bars),
                    'close' : hour_bars[-1]['close'],
                    'volume': sum(b.get('volume', 0) for b in hour_bars),
                }
        except Exception as e:
            log.warning('%s: 1min hour OHLC error: %s', tick, e)

        # ── Running hourly H/L accumulator ────────────────────────────────────
        # Updated HERE (after hour_bars) so on hour change / cold start we can
        # seed from DB bar H/L instead of just the live price.  This eliminates
        # the "h_high = h_low = last" problem on fresh deployments and at hour
        # boundaries — the DB bars already capture extremes since the hour opened.
        if display_price:
            if state['hourly_hour'].get(sid) != cur_hour:
                # New hour or cold start: prefer DB bar range; fall back to live price.
                state['hourly_hour'][sid] = cur_hour
                if ohlc:
                    state['hourly_high'][sid] = max(ohlc['high'], display_price)
                    state['hourly_low'][sid]  = min(ohlc['low'],  display_price)
                else:
                    # No closed bars yet (first ~60s of a new hour)
                    state['hourly_high'][sid] = display_price
                    state['hourly_low'][sid]  = display_price
            else:
                state['hourly_high'][sid] = max(state['hourly_high'].get(sid, display_price), display_price)
                state['hourly_low'][sid]  = min(state['hourly_low'].get(sid,  display_price), display_price)

        # Merge accumulator (tracks live ticks between 1-min bar closes) with ohlc.
        # acc_high / acc_low extend the DB bars to cover the developing bar's extremes.
        # For stocks at h9: the accumulator was seeded at 9:00am and has been running
        # through pre-market.  Ignore it and use RTH ohlc + live price only, so that
        # pre-market prices don't inflate h_high beyond the actual RTH range.
        if not tick.startswith('/') and cur_hour == 9:
            if ohlc:
                acc_high = max(ohlc['high'], display_price)
                acc_low  = min(ohlc['low'],  display_price)
            else:
                # First ~60s of RTH: 9:30am bar hasn't closed yet.
                # Use live price only — avoids pre-market accumulator contamination.
                acc_high = display_price
                acc_low  = display_price
        else:
            acc_high = state['hourly_high'].get(sid, display_price)
            acc_low  = state['hourly_low'].get(sid,  display_price)

        # Cold-start anchor + accumulator merge (current forming hour, all symbols)
        if not ohlc:
            ohlc = {'open': display_price, 'high': acc_high, 'low': acc_low,
                    'close': display_price, 'volume': 0}
        else:
            ohlc['high'] = max(ohlc['high'], acc_high)
            ohlc['low']  = min(ohlc['low'],  acc_low)

        now_et    = datetime.now(ET)
        et_minute = now_et.hour * 60 + now_et.minute

        # ── Opening bias (all symbols) — gap vs prior close ───────────────────
        # Set ONCE per day at RTH open. For equities Schwab q['open'] is the
        # official 9:30 ET open. For futures rth_open is set from the 9:30 bar.
        # prev_settle = last − net_change works for every instrument type.
        if _is_rth and not state['daily_bias'].get(sid):
            net_chg_now = q.get('net_change', 0)
            q_open_now  = q.get('open', 0)
            if net_chg_now and last:
                _prev = round(last - net_chg_now, 4)
                # Use futures rth_open (9:30 bar) when available, else Schwab openPrice
                _open = state['rth_open'].get(sid) or (q_open_now if not tick.startswith('/') else 0)
                if _open and _prev:
                    state['prev_settle'][sid] = _prev
                    if _open > _prev:
                        state['daily_bias'][sid] = 'LONG'
                    elif _open < _prev:
                        state['daily_bias'][sid] = 'SHORT'

        # ── Clearing Range (CR) ───────────────────────────────────────────────
        # Reset CR each new trading day
        _today_str = now_et.strftime('%Y-%m-%d')
        if state['cr_date'] != _today_str:
            state['cr']         = {}
            state['cr_breached'] = {}
            state['daily_bias']  = {}   # reset bias too — new day
            state['cr_sticky']   = {}   # clear sticky counters — new day
            state['cr_date']    = _today_str

        # Compute CR from 9:30–10:00 AM bars (once, after IB complete)
        if sid not in state['cr']:
            ts_size, _ = vbh_engine._tick(api)
            _cr = _compute_cr(state['1min_today'].get(sid, []), ts_size)
            if _cr:
                state['cr'][sid] = _cr

        # Monitor CR breach → update bias
        # Per original study: breach window = OREnd (10:00) to BreachEnd (10:30) ET.
        # Only the FIRST breach within that 30-min window counts — no flips, no late entries.
        # Once breached, cr_breached stays set all day so NEAR/ENTRY can show on the retreat.
        # INVALIDATION (Option A): if price crosses the OPPOSITE extreme at any time after
        # the window, the CR signal is killed (cr_breached cleared). daily_bias stays stamped
        # at the original breach direction — VBH Phase 1 signals continue unaffected.
        _cr_breach_active = (10 * 60 <= et_minute < 10 * 60 + 30)  # 10:00–10:30 AM ET

        _cr = state['cr'].get(sid)
        if _cr and _cr.get('complete') and last:
            _breached = state['cr_breached'].get(sid)
            if _cr_breach_active and not _breached:
                # First breach within the window — stamp direction on both cr and bias
                if last > _cr['entry_long']:
                    state['cr_breached'][sid] = 'LONG'
                    state['daily_bias'][sid]  = 'LONG'
                elif last < _cr['entry_short']:
                    state['cr_breached'][sid] = 'SHORT'
                    state['daily_bias'][sid]  = 'SHORT'
            elif _breached and not _cr_breach_active:
                # Window closed — watch for opposite-side cross to invalidate CR signal.
                # daily_bias is intentionally left unchanged (asset stays stamped LONG/SHORT).
                if _breached == 'LONG' and last < _cr['entry_short']:
                    state['cr_breached'][sid] = None   # CR signal killed
                elif _breached == 'SHORT' and last > _cr['entry_long']:
                    state['cr_breached'][sid] = None   # CR signal killed

        # ── Legacy daily bias fallback (futures: was already set via rth_open) ─
        # Guard with _is_rth: rth_open / prev_settle carry over from the prior
        # day and would re-stamp yesterday's bias pre-market after the new-day
        # reset clears daily_bias — suppressing LONG signals all pre-market.
        rth_open_val   = state['rth_open'].get(sid, 0)
        prev_settl_val = state['prev_settle'].get(sid)
        if _is_rth and rth_open_val and prev_settl_val and not state['daily_bias'].get(sid):
            if rth_open_val > prev_settl_val:
                state['daily_bias'][sid] = 'LONG'
            elif rth_open_val < prev_settl_val:
                state['daily_bias'][sid] = 'SHORT'
        bias_val = state['daily_bias'].get(sid)

        # ── RTH check ─────────────────────────────────────────────────────────────
        _is_stock         = not tick.startswith('/')
        _is_rth           = now_et.weekday() < 5 and 9 * 60 + 30 <= et_minute < 16 * 60
        # Off-hours stocks: any stock outside 09:30–16:00 ET Mon–Fri.
        # Covers: weekday pre/post-market, weekends, and federal holidays.
        _is_off_hours_stock = _is_stock and not _is_rth

        # ── Off-hours gate (futures only after this point) ────────────────────────
        # Only gate symbols that HAVE stats for some hours but NOT the current one.
        # Bypass for off-hours stocks — they use PREV hour_override below, so the
        # current-hour L3==0 check is irrelevant (hour_override selects h15 stats).
        _sym_stats      = state['stats_agg'].get(sid, {})
        _cur_hour_stats = _sym_stats.get(now_et.hour, (0, 0, 0, 0))
        if _cur_hour_stats[2] == 0 and _sym_stats and not _is_off_hours_stock:
            continue

        # ── Off-hours stock PREV fallback ──────────────────────────────────────────
        # Stocks outside RTH have no live 1-min bars for the current hour, so the
        # OHLC accumulator is flat (h_high == h_low == display_price).  Use the
        # previous session's last 2 RTH hours from ohlc_hourly instead so the VBH
        # box remains visible for pre/post-market planning.
        # Signals are capped to NEUTRAL + is_reference=True below.
        _hour_override = None
        if _is_off_hours_stock:
            _prev_bars = get_prev_rth_hours(sid, n_hours=2)
            if _prev_bars:
                _fb_high = max(b['high'] for b in _prev_bars)
                _fb_low  = min(b['low']  for b in _prev_bars)
                ohlc = {
                    'open'  : _prev_bars[0]['open'],
                    'high'  : _fb_high,
                    'low'   : _fb_low,
                    'close' : _prev_bars[-1]['close'],
                    'volume': 0,
                }
                _hour_override = _prev_bars[-1]['hour_et']
                if not last:
                    last = _prev_bars[-1]['close']
                    display_price = last
                    state['last_price'][sid] = last
            else:
                continue  # no DB history yet for this symbol → CLOSED

        sigs = make_signal(
            tick, api, ohlc, last,
            state['stats_agg'].get(sid, {}),
            state['stats_con'].get(sid, {}),
            daily_bias=bias_val,
            et_minute=et_minute,
            stats_wide=state['stats_wide'].get(sid, {}),
            cr=state['cr'].get(sid),
            cr_breached=state['cr_breached'].get(sid),
            hour_override=_hour_override,
        )
        if sigs:
            # Prev-day fallback is active (no live bars today — weekend, holiday,
            # or intraday data gap).  Mark signals as reference-only so:
            #  • The levels remain visible for planning (signal_state unchanged)
            #  • entry_alert is suppressed (no beep, no bot arming)
            #  • Frontend can render a "PREV" badge instead of the live ENTRY/NEAR dot
            if _hour_override is not None:
                for _s in sigs:
                    _s['is_reference']  = True
                    _s['entry_alert']   = False
                    _s['signal_state']  = 'NEUTRAL'   # PREV = planning ref, not a live trigger
            for s in sigs:
                s['symbol_id']   = sid
                s['signal_hour'] = signal_hour.isoformat()
                s['prev_close']  = round(prev_close, 4)
                s['net_change']  = round(net_chg_raw, 4)
                # Squeeze confirmation — 4 major market futures only
                if tick in MARKET_TICKERS:
                    _sq = state['squeeze'].get(sid)
                    if _sq and 'error' not in _sq:
                        s['sq_state']   = _sq['sq_state']
                        s['mo_state']   = _sq['mo_state']
                        _verd, _rsn     = squeeze_confirms_signal(s['side'], _sq)
                        s['sq_confirm'] = _verd
                        s['sq_reason']  = _rsn
                    else:
                        s['sq_state']   = None
                        s['mo_state']   = None
                        s['sq_confirm'] = 'NEUTRAL'
                        s['sq_reason']  = 'no squeeze data'
                # ── CR sticky: keep NEAR visible for 4 cycles after it first fires ──
                # Prevents the alert from vanishing on the very next refresh if price
                # briefly touches mid and bounces.  Counter resets on each new NEAR/ENTRY.
                if s['model'] == 'CR':
                    _ck = f"{sid}_CR"
                    if s['signal_state'] in ('NEAR', 'ENTRY'):
                        state['cr_sticky'][_ck] = 4   # latch / reset countdown
                    elif state['cr_sticky'].get(_ck, 0) > 0:
                        state['cr_sticky'][_ck] -= 1
                        s['signal_state'] = 'NEAR'    # keep visible this cycle

                # Detect NEAR → ENTRY transition for one-shot beep on frontend
                sk = f"{sid}_{s['model']}"
                prev_st = state['prev_signal_state'].get(sk)
                s['entry_alert'] = (s['signal_state'] == 'ENTRY' and prev_st != 'ENTRY')
                state['prev_signal_state'][sk] = s['signal_state']
            rows.extend(sigs)

    # ── Cross-model cascade promotion ────────────────────────────────────────
    # Each model alerts on its own levels independently — no suppression.
    # However: if a more sensitive model (AGG) is already ENTRY, promote the
    # less sensitive models (CON, WIDE) to at least NEAR so a CON-only or
    # WIDE-only user gets a visible warning that the setup is active one tier up.
    # This does NOT trigger an entry_alert beep — only a visual NEAR badge.
    MODEL_PRIORITY = {'AGG': 0, 'CON': 1, 'WIDE': 2}
    from collections import defaultdict
    best_state: dict = defaultdict(lambda: 'NEUTRAL')  # (symbol, side) → best state across models
    for r in rows:
        key = (r['symbol'], r['side'])
        cur = r.get('signal_state', 'NEUTRAL')
        if MODEL_PRIORITY.get(r['model'], 99) < MODEL_PRIORITY.get(
            next((rr['model'] for rr in rows if (rr['symbol'], rr['side']) == key and rr.get('signal_state') == best_state[key]), 'WIDE'), 99
        ):
            best_state[key] = cur
    # Promote NEUTRAL → NEAR on less-sensitive models when a more sensitive one is ENTRY/NEAR
    for r in rows:
        key = (r['symbol'], r['side'])
        row_state = r.get('signal_state', 'NEUTRAL')
        top_state = best_state[key]
        row_priority = MODEL_PRIORITY.get(r['model'], 99)
        # Only promote if this row is less sensitive AND currently NEUTRAL
        if row_state == 'NEUTRAL' and top_state in ('ENTRY', 'NEAR'):
            # Find the most sensitive model currently ENTRY or NEAR for this symbol/side
            top_model = min(
                (rr for rr in rows if (rr['symbol'], rr['side']) == key and rr.get('signal_state') in ('ENTRY', 'NEAR')),
                key=lambda rr: MODEL_PRIORITY.get(rr['model'], 99),
                default=None,
            )
            if top_model and MODEL_PRIORITY.get(top_model['model'], 99) < row_priority:
                r['signal_state'] = 'NEAR'
                r['entry_alert']  = False   # visual only — no beep

    _STATE_ORDER = {'ENTRY': 0, 'NEAR': 1}
    rows.sort(key=lambda r: (
        _STATE_ORDER.get(r.get('signal_state', ''), 2),   # ENTRY → NEAR → everything else
        r['side'] != 'LONG',                               # LONG before SHORT within tier
        -r['swing_pct'],                                   # most stretched first
    ))
    state['signals'] = rows
    state['last_signal_update'] = datetime.now(ET).isoformat()
    state['status'] = 'live'

    # ── Push to all connected WebSocket clients ────────────────────────────────
    if ws_manager.clients:
        asyncio.create_task(ws_manager.broadcast({
            'type':         'update',
            'signals':      rows,
            'last_updated': state['last_signal_update'],
        }))

    _near_ct  = sum(1 for r in rows if r.get('signal_state') == 'NEAR')
    _entry_ct = sum(1 for r in rows if r.get('signal_state') == 'ENTRY')
    _alert_ct = sum(1 for r in rows if r.get('entry_alert'))
    log.info('Signals refreshed — %d rows | NEAR=%d ENTRY=%d | new_entry_alert=%d',
             len(rows), _near_ct, _entry_ct, _alert_ct)
    if _entry_ct and not _alert_ct:
        _entry_syms = [(r['symbol'], r['model'], r['side']) for r in rows if r.get('signal_state') == 'ENTRY']
        log.debug('ENTRY signals (no alert — already ENTRY last cycle): %s', _entry_syms)

    # ── Persist snapshot to DB so next restart serves data instantly ──────────
    # Only save if we have signals — don't overwrite a good snapshot with an
    # empty array (which happens off-hours when all stocks are RTH-gated).
    if rows:
        try:
            from db import cache_set
            cache_set('signals_snapshot', {
                'signals'     : rows,
                'last_updated': state['last_signal_update'],
            })
        except Exception as e:
            log.warning('Signal snapshot save error: %s', e)

    # ── Entry log — persist every NEAR→ENTRY transition for forward testing ──────
    # Build a (symbol, model) → signal lookup so we can resolve cross-model targets.
    # When a WIDE entry fires we capture AGG and CON targets as T1/T2 exit tiers.
    _sig_map: dict[tuple, dict] = {(r['symbol'], r['model']): r for r in rows}

    def _tiered_targets(r: dict) -> dict:
        """Return T1/T2/T3 target levels and initial outcomes for this signal row."""
        sym   = r['symbol']
        model = r['model']
        side  = r['side']
        entry = r['entry']

        agg  = _sig_map.get((sym, 'AGG'),  {})
        con  = _sig_map.get((sym, 'CON'),  {})
        wide = _sig_map.get((sym, 'WIDE'), {})

        # T1 = AGG target (always), T2 = CON target, T3 = WIDE target
        t1_px = agg.get('target')   or (r['target'] if model == 'AGG'  else None)
        t2_px = con.get('target')   if model in ('CON', 'WIDE') else None
        t3_px = wide.get('target')  if model == 'WIDE' else None

        def _pnl(tgt):
            if tgt is None or entry is None:
                return None
            return round((tgt - entry) if side == 'LONG' else (entry - tgt), 4)

        return {
            'target_t1' : t1_px,
            'target_t2' : t2_px,
            'target_t3' : t3_px,
            'pnl_t1'    : _pnl(t1_px),
            'pnl_t2'    : _pnl(t2_px),
            'pnl_t3'    : _pnl(t3_px),
            'outcome_t1': 'OPEN' if t1_px is not None else None,
            'outcome_t2': 'OPEN' if t2_px is not None else None,
            'outcome_t3': 'OPEN' if t3_px is not None else None,
        }

    _entry_log_rows = [
        {
            'fired_at'  : datetime.now(ET).isoformat(),
            'symbol'    : r['symbol'],
            'model'     : r['model'],
            'side'      : r['side'],
            'entry'     : r['entry'],
            'stop'      : r['stop'],
            't1'        : r['t1'],
            'target'    : r['target'],
            'last_price': r['last'],
            'daily_bias': r.get('daily_bias'),
            'hour_et'   : r.get('hour_et'),
            **_tiered_targets(r),
        }
        for r in rows if r.get('entry_alert')
    ]
    if _entry_log_rows:
        try:
            await asyncio.to_thread(insert_entry_log, _entry_log_rows)
            log.info('Entry log: +%d entries (%s)',
                     len(_entry_log_rows),
                     ', '.join(f"{r['symbol']} {r['model']} {r['side']}"
                               for r in _entry_log_rows))
        except Exception as e:
            log.warning('Entry log insert error: %s', e)

    # Persist to DB for future backtesting
    if rows:
        db_rows = [{
            'symbol_id'    : r['symbol_id'],
            'model'        : r['model'],
            'side'         : r['side'],
            'entry'        : r['entry'],
            'stop'         : r['stop'],
            'target'       : r['target'],
            'last_price'   : r['last'],
            'hour_high'    : r['hour_high'],
            'hour_low'     : r['hour_low'],
            'current_range': r['current_range'],
            'typical_range': r['typical_range'],
            'swing_pct'    : r['swing_pct'],
            'signal_hour'  : signal_hour.isoformat(),
        } for r in rows]
        try:
            await asyncio.to_thread(insert_signals, db_rows)
        except Exception as e:
            log.warning('Signal insert error: %s', e)


STRIP_REFRESH_SECS    = 300         # refresh strip RTH opens every 5 min (was 3600)
HOLDINGS_REFRESH_SECS = 86400       # refresh ETF holdings once per day
MIN1_REFRESH_SECS     = 60          # refresh 1-min bars for all futures
CONTRACT_REFRESH_SECS = 3600        # re-check active contracts once per hour


def _active_contract(ticker: str) -> str:
    """Return the Schwab-confirmed active contract symbol, e.g. '/GCQ26'.
    Falls back to computed front_month_code if not yet populated."""
    return state['active_contracts'].get(ticker) or front_month_code(ticker)


async def refresh_active_contracts() -> None:
    """Determine the active front-month contract by comparing CME volume.

    Algorithm:
      1. For each futures ticker, generate 3 candidate contracts:
           c0 = formula front month (front_month_code)
           c1 = next listed month after c0
           c2 = next listed month after c1
      2. Quote all candidates in ONE Schwab call (batched across all tickers).
      3. Pick the candidate with the highest totalVolume per ticker.
         Volume is always concentrated in the active contract — it never lies.
      4. If ALL three return volume=0 (exchange closed / holiday) → keep c0
         so we never drift to a wrong contract based on stale/absent data.

    Called at startup and every hour — automatic roll detection with no
    manual calendar to maintain.
    """
    futures = [s for s in state['symbols'] if s['ticker'].startswith('/')]
    if not futures:
        return

    # ── Step 1: build 3 candidate contracts per ticker ────────────────────────
    candidates: dict[str, list[str]] = {}
    for sym in futures:
        tick = sym['ticker']
        c0   = front_month_code(tick)
        c1   = next_contract_month(tick, c0)
        c2   = next_contract_month(tick, c1)
        candidates[tick] = [c0, c1, c2]

    # ── Step 2: quote all candidates in a single Schwab call ─────────────────
    all_syms = list({c for cs in candidates.values() for c in cs})
    try:
        raw = await asyncio.to_thread(get_quotes, all_syms)
    except Exception as e:
        log.warning('refresh_active_contracts error: %s — falling back to formula', e)
        fallback = {sym['ticker']: front_month_code(sym['ticker']) for sym in futures}
        state['active_contracts'].update(fallback)
        log.info('Active contracts (formula fallback %d): %s', len(fallback),
                 '  '.join(f'{k}→{v}' for k, v in sorted(fallback.items())))
        return

    # ── Step 3: pick highest-volume candidate per ticker ─────────────────────
    confirmed: dict[str, str] = {}
    for tick, cs in candidates.items():
        vols    = [(raw.get(c, {}).get('volume') or 0, c) for c in cs]
        best_v, best_c = max(vols, key=lambda x: x[0])
        formula = cs[0]

        if best_v == 0:
            # Exchange closed / holiday — all volumes zero, keep formula guess
            confirmed[tick] = formula
        else:
            confirmed[tick] = best_c
            if best_c != formula:
                log.info('  %s  volume roll detected: %s (vol=%s) beats formula %s (vol=%s)',
                         tick, best_c, best_v, formula,
                         raw.get(formula, {}).get('volume') or 0)

    state['active_contracts'].update(confirmed)
    log.info('Active contracts (%d): %s', len(confirmed),
             '  '.join(f'{k}→{v}' for k, v in sorted(confirmed.items())))


async def refresh_hourly_hl():
    """Lightweight quote fetch (every 10s) to keep hourly H/L accumulators current.

    Fetches only the lastPrice for all active futures and updates
    state['hourly_high'] / state['hourly_low'] so current_hour_ohlc in
    refresh_signals() matches TOS tick-by-tick tracking as closely as possible.
    Does NOT update signals, 1-min bars, or any other state.
    """
    futures_syms = [s for s in state['symbols'] if s['ticker'].startswith('/')]
    if not futures_syms:
        return

    try:
        quote_syms = [s['schwab_symbol'] for s in futures_syms]
        raw = await asyncio.to_thread(get_quotes, quote_syms)
    except Exception as e:
        log.debug('HL refresh quote error: %s', e)
        return

    cur_hour = datetime.now(ET).hour
    for sym in futures_syms:
        sid = sym['id']
        q   = raw.get(sym['schwab_symbol'], {})
        px  = q.get('last', 0)
        if not px:
            continue
        if state['hourly_hour'].get(sid) != cur_hour:
            state['hourly_hour'][sid] = cur_hour
            state['hourly_high'][sid] = px
            state['hourly_low'][sid]  = px
        else:
            state['hourly_high'][sid] = max(state['hourly_high'].get(sid, px), px)
            state['hourly_low'][sid]  = min(state['hourly_low'].get(sid,  px), px)


async def refresh_all_1min():
    """
    Fetch and store 1-min bars for ALL active symbols (futures + stocks/ETFs) every cycle.

    Two-stage design:
      Stage 1 — fetch all 44 symbols concurrently (one asyncio task per symbol).
      Stage 2 — write results to DB sequentially (avoids DB contention).

    Concurrent fetches mean wall-clock time is bounded by the slowest single
    Schwab response (~15-20s) regardless of symbol count — adding 28 stocks
    costs essentially nothing extra vs futures-only.

    This gives accurate hourly H/L for every signal symbol, not just futures.
    Stocks/ETFs previously relied on a one-time startup backfill and the live-
    quote accumulator, missing closed 1-min bar extremes mid-session.
    """
    all_syms = state['symbols']
    if not all_syms:
        return

    # Limit concurrency to avoid exhausting the thread pool on cancellation.
    # When asyncio.wait_for() cancels this task, executor futures keep running.
    # With 10 concurrent tasks max, at most 10 zombie threads remain — well within
    # the default pool size and small enough to clear quickly.
    _sem = asyncio.Semaphore(10)

    # Stage 1 — bounded-concurrency fetches for all symbols
    async def _fetch(sym: dict) -> tuple[int, list | None]:
        tick = sym['ticker']
        sid  = sym['id']
        async with _sem:
            try:
                api_sym = _active_contract(tick) if tick.startswith('/') else sym['schwab_symbol']
                candles = await asyncio.to_thread(get_session_bars, api_sym)
                candles = candles or []
                # Update in-memory cache IMMEDIATELY — do not wait for asyncio.gather to
                # return.  With 147 symbols and Semaphore(10), the full gather takes
                # ~75s but the wait_for timeout is 45s.  If we defer the cache write to
                # the post-gather loop it NEVER runs (gather is cancelled), leaving every
                # stock as None and triggering the PREV fallback during live RTH.
                # Writing here means each symbol's cache is current as soon as its fetch
                # completes, regardless of whether the overall gather finishes.
                state['1min_today'][sid] = candles
                return sid, candles
            except Exception as e:
                log.warning('1min fetch %s: %s', tick, e)
                # Return None (not []) so the cache update below can tell the difference
                # between "API/auth error — preserve existing bars" vs "market closed — [] is correct".
                # Do NOT touch state['1min_today'][sid] here — keep the previous bars.
                return sid, None

    results = await asyncio.gather(*[_fetch(s) for s in all_syms])

    # Stage 2 — write to DB concurrently.  In-memory cache was already updated
    # inside _fetch above; this stage is DB persistence only.
    stored = 0

    async def _upsert(sid: int, candles: list) -> bool:
        try:
            rows = _candles_to_1min_rows(sid, candles)
            await asyncio.to_thread(upsert_1min, rows)
            return True
        except Exception as e:
            log.warning('1min upsert sid=%s: %s', sid, e)
            return False

    upsert_tasks = []
    for sid, candles in results:
        if candles is None:
            # API / auth error — cache already preserved inside _fetch (no write happened).
            pass
        if candles:
            upsert_tasks.append(_upsert(sid, candles))

    upsert_results = await asyncio.gather(*upsert_tasks)
    stored = sum(upsert_results)

    futures_n = sum(1 for s in all_syms if s['ticker'].startswith('/'))
    stocks_n  = len(all_syms) - futures_n
    log.info('1-min bars refreshed — %d/%d symbols stored (%d futures, %d stocks/ETFs)',
             stored, len(all_syms), futures_n, stocks_n)


def _parse_holdings_df(df) -> list[dict]:
    """Extract a clean list of holdings from a yfinance top_holdings DataFrame.
    Handles NaN, string percentages, and varying column names across yfinance versions."""
    import math
    if df is None or (hasattr(df, 'empty') and df.empty):
        return []
    holdings = []
    for i, (symbol, row) in enumerate(df.iterrows()):
        if i >= 10:
            break

        # ── Ticker normalisation ──────────────────────────────────────
        import re as _re
        ticker_raw = str(symbol).upper()
        # Yahoo Finance uses BRK-B for class shares; Schwab uses BRK/B
        ticker_str = _re.sub(r'-([A-Z])$', r'/\1', ticker_raw)
        # Strip exchange suffixes (.TA, .L, .HK, .TO, .AX, etc.)
        ticker_str = _re.sub(r'\.[A-Z]{1,4}$', '', ticker_str)

        # ── Company name ─────────────────────────────────────────────
        name = ''
        for col in ('holdingName', 'name', 'longName', 'shortName'):
            raw = row.get(col, '')
            if raw and str(raw) not in ('nan', 'None', ''):
                candidate = str(raw).strip()
                # Skip if yfinance echoed back the ticker symbol as the name
                if candidate.upper() in (ticker_raw, ticker_str):
                    continue
                name = candidate
                break

        # ── Weight ───────────────────────────────────────────────────
        weight = 0.0
        for col in ('holdingPercent', 'percent', 'pct', 'weight'):
            raw = row.get(col)
            if raw is None:
                continue
            try:
                # Handle string like "23.40%" or "0.234"
                if isinstance(raw, str):
                    raw = raw.strip().rstrip('%')
                f = float(raw)
                if math.isnan(f):
                    continue
                # Normalize: ≤1.0 means it's a decimal fraction → multiply by 100
                weight = round(f * 100 if f <= 1.0 else f, 2)
                break
            except (ValueError, TypeError):
                continue

        holdings.append({
            'rank'  : i + 1,
            'ticker': str(symbol).upper(),
            'name'  : name[:40],
            'weight': weight,
        })
    return holdings


async def refresh_etf_holdings():
    """Fetch top-10 holdings for every sector/industry ETF from Yahoo Finance
    and persist to app_cache.  Fund-data endpoint is separate from real-time
    quotes so Railway IP rate-limiting is not an issue at daily frequency."""
    import yfinance as yf

    tickers = list(SECTOR_TICKERS)
    refreshed = 0
    log.info('Refreshing ETF holdings for %d tickers…', len(tickers))

    for ticker in tickers:
        try:
            def _fetch(t=ticker):
                return yf.Ticker(t).funds_data.top_holdings

            df       = await asyncio.to_thread(_fetch)
            holdings = _parse_holdings_df(df)

            if holdings:
                set_etf_holdings(ticker, holdings)
                refreshed += 1
                log.info('  %-6s  %d holdings cached (top weight=%.2f%%)',
                         ticker, len(holdings), holdings[0]['weight'] if holdings else 0)
            else:
                log.warning('  %-6s  holdings empty / unavailable', ticker)
        except Exception as e:
            log.warning('  %-6s  holdings ERROR: %s', ticker, e)

        await asyncio.sleep(1.5)   # gentle on Yahoo Finance

    log.info('ETF holdings refresh done — %d/%d', refreshed, len(tickers))


# ── Daily candle batch loader ──────────────────────────────────────────────────

def _schwab_daily_to_rows(ticker: str, candles: list[dict]) -> list[dict]:
    """Convert Schwab daily candles to ticker_candles_daily rows."""
    rows = []
    for c in candles:
        dt = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        rows.append({
            'ticker'  : ticker,
            'bar_date': dt.date().isoformat(),
            'open'    : float(c['open']),
            'high'    : float(c['high']),
            'low'     : float(c['low']),
            'close'   : float(c['close']),
            'volume'  : int(c.get('volume') or 0),
        })
    return rows


async def refresh_daily_candles(incremental: bool = False) -> None:
    """
    Batch-fetch 90-day daily candles for all ETF holdings + watchlist stocks.

    incremental=False (startup / full refresh):
      Fetches 90 days for every ticker. ~200 tickers × 10 concurrent = ~30 s.

    incremental=True (4:30 PM ET daily close):
      Fetches only the last 2 days for each ticker (picks up today's close).
      Invalidates _fib_cache so next request recomputes from fresh data.
    """
    try:
        holding_tickers  = await asyncio.to_thread(get_etf_holding_tickers)
        watchlist_stocks = [s['schwab_symbol'] for s in state.get('symbols', [])
                            if not s['ticker'].startswith('/')]
        all_tickers = sorted(set(holding_tickers + watchlist_stocks))
    except Exception as e:
        log.warning('refresh_daily_candles: ticker load failed: %s', e)
        return

    days = 2 if incremental else 90
    label = 'incremental' if incremental else 'full'
    log.info('Daily candle refresh (%s): %d tickers, %d days…', label, len(all_tickers), days)

    sem = asyncio.Semaphore(10)   # 10 concurrent Schwab requests
    ok = 0

    async def fetch_one(ticker: str) -> None:
        nonlocal ok
        async with sem:
            try:
                raw  = await asyncio.to_thread(get_daily_candles, ticker, days)
                rows = _schwab_daily_to_rows(ticker, raw)
                if rows:
                    await asyncio.to_thread(upsert_daily_candles, rows)
                    ok += 1
                # Invalidate in-memory Fib cache so next request recomputes
                if incremental and ticker in _fib_cache:
                    del _fib_cache[ticker]
                await asyncio.sleep(0.1)
            except Exception as e:
                log.debug('daily candle %s: %s', ticker, e)

    await asyncio.gather(*[fetch_one(t) for t in all_tickers])
    log.info('Daily candle refresh (%s) done — %d/%d tickers stored', label, ok, len(all_tickers))

    # After full refresh, warm the Fib cache for all tickers from DB
    if not incremental:
        await _warm_fib_cache(all_tickers)


async def refresh_swing_candles() -> None:
    """Fetch last 2 days of daily candles for ticker_universe symbols NOT in symbols table.
    Runs at 4:30 PM ET so swing scan at 5:30 PM always uses fresh data."""
    try:
        from db import get_db as _get_db
        _db = _get_db()
        tu = {r['ticker'] for r in _db.table('ticker_universe').select('ticker').execute().data}
        sy = {r['ticker'] for r in _db.table('symbols').select('ticker').execute().data}
        swing_only = sorted(tu - sy)
    except Exception as e:
        log.warning('refresh_swing_candles: symbol load failed: %s', e)
        return

    log.info('Swing candle refresh: %d swing-only tickers, 2 days…', len(swing_only))
    sem = asyncio.Semaphore(10)
    ok = 0

    async def fetch_one(ticker: str) -> None:
        nonlocal ok
        async with sem:
            try:
                raw  = await asyncio.to_thread(get_daily_candles, ticker, 2)
                rows = _schwab_daily_to_rows(ticker, raw)
                if rows:
                    await asyncio.to_thread(upsert_daily_candles, rows)
                    ok += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                log.debug('swing candle %s: %s', ticker, e)

    await asyncio.gather(*[fetch_one(t) for t in swing_only])
    log.info('Swing candle refresh done — %d/%d tickers stored', ok, len(swing_only))


async def _warm_fib_cache(tickers: list[str]) -> None:
    """Pre-compute Fib levels using ONE batch DB query — zero per-ticker round-trips."""
    if not tickers:
        return
    log.info('Warming Fib cache for %d tickers (batch query)…', len(tickers))
    try:
        all_bars = await asyncio.to_thread(get_daily_candles_batch, tickers, 90)
    except Exception as e:
        log.warning('Fib cache warm batch query failed: %s', e)
        return
    MIN_BARS = 60   # must match the check in get_sr_batch
    warmed = 0
    skipped = 0
    for ticker, rows in all_bars.items():
        if not rows:
            continue
        if len(rows) < MIN_BARS:
            # Not enough history yet — leave uncached so get_sr_batch falls back
            # to Schwab on the next request and gets the proper 90-day range.
            log.debug('Fib warm skip %s: only %d bars < %d minimum', ticker, len(rows), MIN_BARS)
            skipped += 1
            continue
        try:
            bars = [{'bar_time': r['bar_date'] + 'T00:00:00+00:00',
                     'high': float(r['high']), 'low': float(r['low']),
                     'close': float(r['close']), 'open': float(r['open'])}
                    for r in rows]
            sr = _compute_fib_sr(bars)
            _fib_cache_set(ticker, {
                'resistance'  : sr['resistance'],
                'support'     : sr['support'],
                'candle_price': sr['current_price'],
                'fib_high'    : sr['fib_high'],
                'fib_low'     : sr['fib_low'],
                'direction'   : sr.get('direction', 'unknown'),
                'bars'        : sr['bars'],
            })
            warmed += 1
        except Exception as e:
            log.debug('warm fib %s: %s', ticker, e)
    log.info('Fib cache warmed — %d/%d tickers ready (%d skipped <60 bars)', warmed, len(tickers), skipped)


async def refresh_strip_opens():
    """Fetch RTH-only daily candles for the 11 SPDR ETFs and store today's 9:30 open.
    Uses needExtendedHoursData=false so open = true RTH 9:30 open, matching TOS exactly.

    Also sets state['strip_session_date'] to today's date when a real RTH session is
    found — used by get_industries() to detect holidays (no session = use prev_close
    instead of live price, so extended-hours movement doesn't corrupt the strip)."""
    ticker_map = {s['ticker']: s for s in state['symbols']}
    now_et     = datetime.now(ET)
    today_et   = now_et.date()
    et_min     = now_et.hour * 60 + now_et.minute
    # Guard: during live RTH, refresh_signals() owns rth_open for STRIP_TICKERS via the
    # live quote openPrice field, which is more accurate than a daily candle's open.
    # Do NOT skip after RTH closes — a post-close restart would leave rth_open=0 all night.
    is_rth = now_et.weekday() < 5 and (9 * 60 + 30) <= et_min < 16 * 60
    refreshed  = 0
    session_found = False   # True once we confirm today has a real RTH candle

    for etf in STRIP_ETFS:
        tick = etf['ticker']
        sym  = ticker_map.get(tick)
        if not sym:
            continue
        sid = sym['id']
        try:
            candles = await asyncio.to_thread(get_daily_candles, tick, 5)
            if not candles:
                continue
            # Prefer today's candle; fall back to most recent if today's hasn't opened yet
            chosen = None
            for c in reversed(candles):
                dt = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET).date()
                if dt == today_et:
                    chosen = c
                    session_found = True   # real session exists today
                    break
            if chosen is None:
                chosen = candles[-1]   # holiday / pre-market: use last session as placeholder

            # strip_prev_close — prior RTH session's close, used as the % baseline.
            # Derived from the most recent candle whose date is strictly before today.
            # Updated every 5 min so the industries strip resets correctly at the start
            # of each new day, independent of the 24h compute_all_stats() cycle.
            prior_candle = next(
                (c for c in reversed(candles)
                 if datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET).date() < today_et),
                None
            )
            if prior_candle and prior_candle.get('close'):
                state['strip_prev_close'][sid] = round(prior_candle['close'], 4)

            if chosen['open']:
                # During live RTH skip: refresh_signals() will set the more accurate value.
                # Pre-market AND post-close: always write so a server restart doesn't leave
                # rth_open=0 and the entire industries strip blank.
                if tick not in STRIP_TICKERS or not is_rth:
                    state['rth_open'][sid] = round(chosen['open'], 4)
                # Outside RTH: seed last_price from TODAY's confirmed candle close only.
                # Never use the fallback (yesterday's candle) — that would show yesterday's
                # close as "current price" and produce a wrong pct until the next cycle.
                if not is_rth and session_found and chosen.get('close'):
                    state['last_price'][sid] = round(chosen['close'], 4)
                refreshed += 1
        except Exception as e:
            log.warning('refresh_strip_opens %s: %s', tick, e)
        await asyncio.sleep(0.3)

    # Persist whether today has a real RTH session so get_industries() can use it
    if session_found:
        state['strip_session_date'] = today_et
    # (Don't clear it if not found — avoids a race on the very first tick after open)
    log.info('Strip RTH opens refreshed — %d/%d ETFs (session_today=%s)',
             refreshed, len(STRIP_ETFS), session_found)

    # MAG10 live prices are refreshed separately via refresh_mag10_prices() every 60s
    # using netPercentChangeInDouble from live quotes — no daily candles needed here.


async def refresh_ytd():
    """Compute YTD % (Jan-1 open → current price) for all SECTOR_TICKERS + SPY.
    Results cached in state['ytd'] and served instantly from /api/sector-ytd."""
    tickers     = list(SECTOR_TICKERS | {'SPY'})
    current_year = datetime.now(ET).year
    log.info('Refreshing YTD for %d tickers…', len(tickers))

    # Live quotes for current prices (one batch call)
    try:
        quotes = await asyncio.to_thread(get_quotes, tickers)
    except Exception as e:
        log.warning('refresh_ytd quotes error: %s', e)
        quotes = {}

    refreshed = 0
    for ticker in tickers:
        try:
            candles = await asyncio.to_thread(get_daily_candles, ticker, 366)
            if not candles:
                continue
            year_candles = [
                c for c in candles
                if datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
                          .astimezone(ET).year == current_year
            ]
            if not year_candles:
                continue
            jan_open = year_candles[0]['open']
            q        = quotes.get(ticker, {})
            current  = (q.get('last') or 0) or year_candles[-1]['close']
            if jan_open and current:
                state['ytd'][ticker] = round((current - jan_open) / jan_open * 100, 2)
                refreshed += 1
        except Exception as e:
            log.warning('YTD %s: %s', ticker, e)
        await asyncio.sleep(0.3)   # gentle on Schwab

    log.info('YTD refresh done — %d/%d tickers', refreshed, len(tickers))


async def refresh_mag10_prices():
    """Fetch live Schwab quotes for all MAG10 component stocks.
    MAG10 index = Σ(last / div × weight)  — same formula as TOS ThinkScript.
    pct = (idx_now - idx_prev) / idx_prev × 100
    prev_close = last - net_change  (Schwab dollar net change from prior session)."""
    tickers = [c['ticker'] for c in MAG10_COMPONENTS]
    try:
        quotes = await asyncio.to_thread(get_quotes, tickers)
        for t, q in quotes.items():
            last       = float(q.get('last') or 0)
            net_change = float(q.get('net_change') or 0)
            if not last:
                continue   # no price yet — keep previous values
            state['mag10_last'][t]       = last
            state['mag10_prev_close'][t] = round(last - net_change, 4)
        log.debug('MAG10 prices refreshed — %d/%d tickers', len(state['mag10_last']), len(tickers))
    except Exception as e:
        log.warning('refresh_mag10_prices: %s', e)


def _seed_state_stats_from_db() -> None:
    """Instantly populate state['stats_agg'], state['stats_con'], and state['stats_wide']
    from the vbh_engine DB cache (_stats_db) already loaded at startup.

    compute_stats*() check _stats_db first and return DB rows without needing Schwab
    candles — so calling them with [] gives us the DB-backed levels in milliseconds
    rather than waiting 3-10 min for 44 Schwab candle fetches.

    state['prev_close'] and state['last_price'] are seeded by refresh_signals()
    from live Schwab quotes, so they don't need compute_all_stats() either.
    """
    seeded = 0
    for sym in state['symbols']:
        sid = sym['id']
        api = sym['schwab_symbol']
        try:
            agg  = compute_stats([], api)           # uses _stats_db → instant
            con  = compute_stats_con([], api)       # uses _stats_db → instant
            wide = compute_stats_wide([], api)      # uses _stats_db → instant
            if agg or con:
                state['stats_agg'][sid]  = agg
                state['stats_con'][sid]  = con
                state['stats_wide'][sid] = wide
                seeded += 1
        except Exception as e:
            log.warning('_seed_state_stats_from_db %s: %s', sym['ticker'], e)
    log.info('Stats seeded from DB for %d/%d symbols (instant, no Schwab calls)',
             seeded, len(state['symbols']))


async def check_entry_log_outcomes() -> None:
    """Grade entry_log rows against current prices — tiered (T1 / T2 / T3) + stop.

    Runs every 30s alongside the signal refresh.  For each row still open:
      • Stamps 1h / 4h / EOD price snapshots as they come due.
      • Grades each tier independently (T1=AGG, T2=CON, T3=WIDE target).
      • Marks HIT_STOP if stop crosses before any remaining open tier.
      • Marks EXPIRED after 48h if still open.
      • Overall outcome = furthest tier hit: HIT_T3 > HIT_T2 > HIT_T1 > HIT_STOP > OPEN.
    """
    from db import get_open_entry_log_rows, update_entry_log_outcome

    rows = await asyncio.to_thread(get_open_entry_log_rows)
    if not rows:
        return

    now_utc = datetime.now(timezone.utc)
    now_et  = datetime.now(ET)

    # Build symbol → current price map from in-memory state
    ticker_to_price: dict[str, float] = {}
    for sym in state.get('symbols', []):
        sid  = sym['id']
        tick = sym['ticker']
        px   = state['last_price'].get(sid)
        if px:
            ticker_to_price[tick] = px

    for row in rows:
        symbol   = row['symbol']
        side     = row['side']
        entry_px = row['entry']
        stop_px  = row['stop']
        fired_at = datetime.fromisoformat(row['fired_at'].replace('Z', '+00:00'))

        current_px = ticker_to_price.get(symbol)
        if not current_px:
            continue

        updates: dict = {}
        elapsed_h = (now_utc - fired_at).total_seconds() / 3600

        # ── Price snapshots ─────────────────────────────────────────────────
        if elapsed_h >= 1.0 and not row.get('snap_1h_at'):
            updates['price_1h']   = current_px
            updates['snap_1h_at'] = now_utc.isoformat()
        if elapsed_h >= 4.0 and not row.get('snap_4h_at'):
            updates['price_4h']   = current_px
            updates['snap_4h_at'] = now_utc.isoformat()
        fired_et = fired_at.astimezone(ET)
        if (now_et.date() >= fired_et.date() and now_et.hour >= 16
                and not row.get('snap_eod_at')):
            updates['price_eod']   = current_px
            updates['snap_eod_at'] = now_utc.isoformat()

        # ── Helpers ──────────────────────────────────────────────────────────
        def _hit_target(tgt) -> bool:
            if tgt is None: return False
            return current_px >= tgt if side == 'LONG' else current_px <= tgt

        def _hit_stop() -> bool:
            if stop_px is None: return False
            return current_px <= stop_px if side == 'LONG' else current_px >= stop_px

        # ── Per-tier grading ─────────────────────────────────────────────────
        t1_px = row.get('target_t1')
        t2_px = row.get('target_t2')
        t3_px = row.get('target_t3')

        if row.get('outcome_t1') == 'OPEN' and _hit_target(t1_px):
            updates['outcome_t1'] = 'HIT'
            updates['t1_hit_at']  = now_utc.isoformat()

        if row.get('outcome_t2') == 'OPEN' and _hit_target(t2_px):
            updates['outcome_t2'] = 'HIT'
            updates['t2_hit_at']  = now_utc.isoformat()

        if row.get('outcome_t3') == 'OPEN' and _hit_target(t3_px):
            updates['outcome_t3'] = 'HIT'
            updates['t3_hit_at']  = now_utc.isoformat()

        # Resolve current tier outcomes (merging updates with existing row data)
        oc_t1 = updates.get('outcome_t1', row.get('outcome_t1'))
        oc_t2 = updates.get('outcome_t2', row.get('outcome_t2'))
        oc_t3 = updates.get('outcome_t3', row.get('outcome_t3'))

        # ── Overall outcome: furthest tier hit, else stop/expired ────────────
        current_overall = row.get('outcome', 'OPEN')
        new_overall     = current_overall

        stopped = _hit_stop() and current_overall not in ('HIT_T1', 'HIT_T2', 'HIT_T3')

        if oc_t3 == 'HIT':
            new_overall = 'HIT_T3'
        elif oc_t2 == 'HIT':
            new_overall = 'HIT_T2'
        elif oc_t1 == 'HIT':
            new_overall = 'HIT_T1'
        elif stopped:
            new_overall = 'HIT_STOP'
        elif elapsed_h >= 48 and current_overall == 'OPEN':
            new_overall = 'EXPIRED'

        if new_overall != current_overall:
            pnl = round(
                (current_px - entry_px) if side == 'LONG' else (entry_px - current_px), 4
            )
            updates['outcome']    = new_overall
            updates['outcome_at'] = now_utc.isoformat()
            updates['pnl_pts']    = pnl

        if updates:
            await asyncio.to_thread(update_entry_log_outcome, row['id'], updates)


async def background_loop():
    # ── Schwab token persistence ──────────────────────────────────────────────
    # Schwab uses ROTATING refresh tokens — each use generates a new one.
    # The Railway env var holds the original token which becomes stale after
    # the first use.  Load the latest persisted token from DB (if available)
    # so restarts / new deploys always start with a valid token.
    try:
        from db import cache_get, cache_set
        saved_token = cache_get('schwab_refresh_token')
        if saved_token and saved_token.get('token'):
            _schwab_token_cache['refresh_token'] = saved_token['token']
            log.info('Schwab refresh token loaded from DB cache')
            state['token_state'] = 'loaded_from_db'
        else:
            log.warning('Schwab token: DB cache empty — using env var (may be stale)')
            state['token_state'] = 'using_env_var'
        # Register callback so every future rotation is persisted automatically.
        # Also record the issue timestamp so we can warn before expiry.
        def _persist_token(t: str) -> None:
            cache_set('schwab_refresh_token', {
                'token':    t,
                'issued_at': datetime.now(timezone.utc).isoformat(),
            })
        set_token_refresh_callback(_persist_token)
        # Back-fill issued_at if it's missing (first run after this deploy)
        if saved_token and saved_token.get('token') and not saved_token.get('issued_at'):
            cache_set('schwab_refresh_token', {
                'token':    saved_token['token'],
                'issued_at': datetime.now(timezone.utc).isoformat(),
            })
            log.info('Schwab token: issued_at back-filled')
    except Exception as e:
        log.warning('Schwab token cache load error: %s', e)
        state['token_state'] = f'load_failed: {e}'

    state['symbols'] = get_active_symbols()
    log.info('Loaded %d symbols', len(state['symbols']))

    # ── Load VBH levels from Supabase into vbh_engine module cache ────────────
    # Must run before compute_all_stats() so the DB cache is available as the
    # preferred source for compute_stats() / compute_stats_con().
    # Falls back silently to hardcoded 2022 tables if the DB is empty or unavailable.
    db_loaded = vbh_engine.load_stats_from_db()
    if db_loaded:
        n_tickers = len(vbh_engine._stats_db)
        log.info('VBH stats loaded from DB — %d ticker(s) ready', n_tickers)
    else:
        log.warning('VBH stats DB load failed or empty — using hardcoded 2022 tables')

    # ── Load last signal snapshot from DB so HTTP is useful immediately ───────
    # compute_all_stats takes 60-90s; this pre-seeds state with yesterday's
    # signals so the dashboard is not blank during the warm-up window.
    try:
        from db import cache_get
        snapshot = cache_get('signals_snapshot')
        if snapshot and snapshot.get('signals'):
            state['signals']           = snapshot['signals']
            state['last_signal_update'] = snapshot.get('last_updated', '')
            state['status']            = 'cached'
            # Seed prev_signal_state so existing ENTRY/NEAR signals don't
            # re-fire entry_alert on the first refresh cycle after restart.
            for _sig in snapshot['signals']:
                _sk = f"{_sig.get('symbol_id')}_{_sig.get('model')}"
                state['prev_signal_state'][_sk] = _sig.get('signal_state', '')
            log.info('Pre-seeded %d cached signals from DB (last: %s)',
                     len(state['signals']), state['last_signal_update'])
    except Exception as e:
        log.warning('Signal snapshot load error: %s', e)

    # ── Validate front-month contract letters for all active futures ────────────
    futures_syms = [s for s in state['symbols'] if s['ticker'].startswith('/')]
    log.info('Front-month contracts for %d active futures:', len(futures_syms))
    for _s in futures_syms:
        _contract = front_month_code(_s['ticker'])
        log.info('  %-8s  →  %s', _s['ticker'], _contract)

    try:
        await asyncio.wait_for(refresh_active_contracts(), timeout=30)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning('startup refresh_active_contracts: %s', e)

    # ── Seed stats from DB instantly, then compute fresh in background ─────────
    # compute_all_stats() fetches 90-day Schwab candles for 44 symbols and can
    # take 3-10+ minutes, blocking the first signal refresh entirely.
    # vbh_engine.load_stats_from_db() already loaded _stats_db at startup.
    # compute_stats() / compute_stats_con() use _stats_db first — so we only
    # need to call them with empty candles to populate state['stats_agg/con'].
    _seed_state_stats_from_db()   # instant — no network calls
    asyncio.create_task(compute_all_stats())  # refresh from Schwab in background

    try:
        await asyncio.wait_for(refresh_strip_opens(), timeout=60)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning('startup refresh_strip_opens: %s', e)
    try:
        await asyncio.wait_for(refresh_mag10_prices(), timeout=30)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning('startup refresh_mag10_prices: %s', e)
    try:
        await asyncio.wait_for(refresh_all_1min(), timeout=45)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning('startup refresh_all_1min: %s', e)
    try:
        await asyncio.wait_for(refresh_signals(), timeout=90)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning('startup refresh_signals: %s', e)

    # ── Define inner loops BEFORE creating tasks ───────────────────────────────
    async def _hl_loop():
        """Independent 10s loop — updates hourly H/L accumulators between signal refreshes.
        Also pushes a lightweight price update to all WS clients so the browser
        reflects the latest price every 10s instead of waiting for the full 30s refresh.
        """
        while True:
            await asyncio.sleep(HL_REFRESH_SECS)
            try:
                await refresh_hourly_hl()
            except Exception as _hl_e:
                log.warning('_hl_loop error: %s', _hl_e)

            # Price push handled by _price_loop (every 5s) — no duplicate here

    asyncio.create_task(_hl_loop())   # fast H/L accumulator — runs independently at 10s

    async def _price_loop():
        """5-second price-only refresh pushed to WS clients.

        Fetches quotes for:
          • All active futures (always — traders need these real-time)
          • Stocks/ETFs currently in ENTRY or NEAR state (highest priority)
        Then broadcasts a lightweight {type:'prices'} payload.
        No candle processing, no signal recompute — pure quote + push.
        """
        while True:
            await asyncio.sleep(PRICE_REFRESH_SECS)
            if not ws_manager.clients:
                continue   # nobody watching — skip the Schwab call entirely

            try:
                syms = state.get('symbols', [])
                sid_to_ticker = {s['id']: s['ticker'] for s in syms}

                # Fetch ALL active symbols — get_quotes is one batch call regardless of count
                priority = syms   # no filtering — full universe every 5s

                quote_syms = [
                    _active_contract(s['ticker']) if s['ticker'].startswith('/')
                    else s['schwab_symbol']
                    for s in priority
                ]
                quote_key = {
                    (_active_contract(s['ticker']) if s['ticker'].startswith('/') else s['schwab_symbol']): s['id']
                    for s in priority
                }

                raw = await asyncio.to_thread(get_quotes, quote_syms)

                prices: dict[str, float] = {}
                for qs, q in raw.items():
                    last = q.get('last', 0)
                    if not last:
                        continue
                    sid = quote_key.get(qs)
                    if sid is None:
                        continue
                    ticker = sid_to_ticker.get(sid)
                    if ticker:
                        prices[ticker] = round(last, 4)
                        state['last_price'][sid] = round(last, 4)

                if prices:
                    asyncio.create_task(ws_manager.broadcast({
                        'type':   'prices',
                        'prices': prices,
                        'ts':     datetime.now(ET).isoformat(),
                    }))
                    log.debug('Price push: %d symbols → %d WS clients',
                              len(prices), len(ws_manager.clients))
            except Exception as _pe:
                log.warning('_price_loop error: %s', _pe)

    asyncio.create_task(_price_loop())         # 5s real-time price push via WebSocket
    asyncio.create_task(_bot_loop())           # 5s futures bot tick
    asyncio.create_task(_equity_bot_loop())    # 5s equity bot tick

    # Kick off background tasks that don't block startup
    asyncio.create_task(refresh_etf_holdings())
    asyncio.create_task(refresh_ytd())
    asyncio.create_task(refresh_daily_candles(incremental=False))  # full 90-day batch
    asyncio.create_task(_refresh_global_markets())                 # prime Asia + FX immediately
    asyncio.create_task(refresh_stock_profiles())                  # stock fundamentals via yfinance

    last_strip_refresh      = time.time()
    last_holdings_refresh   = time.time()
    last_ytd_refresh        = time.time()
    last_candle_purge       = time.time()
    last_daily_refresh      = time.time()
    last_contract_refresh   = time.time()
    last_global_markets     = 0.0  # force refresh on first tick
    last_daily_close_run    = ''   # 'YYYY-MM-DD' of last 4:30 PM run
    last_1min_agg_run       = ''   # 'YYYY-MM-DD' of last 5:00 PM 1-min → 15-min aggregation
    last_vbh_update_run     = ''   # 'YYYY-MM-DD' of last 5:30 AM VBH table update
    last_vbh_stocks_vbh_run = ''   # 'YYYY-MM-DD' of last Saturday VBH recompute for stocks
    last_profiles_run       = ''   # 'YYYY-MM-DD' of last 6:00 AM stock profiles refresh
    last_archive_run        = ''   # 'YYYY-MM-DD' of last entry_log → archive move
    last_swing_scan_run     = ''   # 'YYYY-MM-DD' of last 5:30 PM swing scan
    last_gex_baseline_run   = ''   # 'YYYY-MM-DD' of last successful GEX baseline (real OI)
    last_gex_baseline_ts    = 0.0  # epoch of last baseline attempt (for 5-min retry rate-limit)
    last_gex_stocks_run     = ''   # 'YYYY-MM-DD' of last 5:30 PM stock GEX snapshot
    last_gex_intraday_ts    = 0.0  # epoch of last 15-min GEX intraday refresh
    GEX_INTRADAY_SECS       = 900  # 15 minutes

    # ── Startup GEX baseline ───────────────────────────────────────────────────
    # If the server restarts on a weekday after 6 AM ET, fire the baseline
    # immediately instead of waiting up to 30s for the first background loop tick.
    # This prevents missed baselines caused by Railway redeploys during market hours.
    _startup_et = datetime.now(ET)
    if _startup_et.weekday() < 5 and _startup_et.hour >= 6:
        async def _startup_gex_baseline():
            try:
                await asyncio.to_thread(_refresh_gex_indices, True)
                log.info('GEX startup baseline saved for %s', GEX_INDEX_SYMBOLS)
            except Exception as _e:
                log.warning('GEX startup baseline error: %s', _e)
        asyncio.create_task(_startup_gex_baseline())
        last_gex_baseline_run = _startup_et.date().isoformat()
        log.info('GEX startup baseline queued (weekday, post-6AM ET)')

    # ── Startup catch-up: daily_update + swing scan ────────────────────────────
    # If Railway redeploys after 4:30/5:30 PM ET, the in-memory guards are reset
    # and the nightly windows are missed.  Check the DB and re-fire if needed.
    _startup_hhmm = _startup_et.hour * 60 + _startup_et.minute
    if _startup_et.weekday() < 5 and _startup_hhmm >= 16 * 60 + 30:
        try:
            _db_startup    = get_db()
            _today_iso     = _startup_et.date().isoformat()
            _today_candles = (_db_startup.table('ticker_candles_daily')
                              .select('ticker').eq('bar_date', _today_iso)
                              .limit(1).execute().data)
            if not _today_candles:
                async def _startup_daily_update():
                    try:
                        import subprocess as _sp
                        await asyncio.to_thread(
                            lambda: _sp.run(
                                ['python3', 'daily_update.py'],
                                cwd=os.path.dirname(__file__),
                            )
                        )
                        log.info('Startup catch-up: daily_update.py complete')
                    except Exception as _e:
                        log.warning('Startup catch-up: daily_update.py error: %s', _e)
                asyncio.create_task(_startup_daily_update())
                last_daily_close_run = _today_iso
                log.info('Startup catch-up: daily_update.py queued (no %s candles in DB)', _today_iso)
        except Exception as _e:
            log.warning('Startup catch-up daily_update check failed: %s', _e)

    if _startup_et.weekday() < 5 and _startup_hhmm >= 17 * 60 + 30:
        try:
            _db_startup  = get_db()
            _today_iso   = _startup_et.date().isoformat()
            _latest_scan = (_db_startup.table('swing_scan_results')
                            .select('scanned_at').order('scanned_at', desc=True)
                            .limit(1).execute().data)
            _scan_date   = (_latest_scan[0]['scanned_at'][:10] if _latest_scan else '')
            if _scan_date != _today_iso:
                async def _startup_swing_scan():
                    try:
                        from scanner import scan_swing as _scan_swing
                        await asyncio.to_thread(lambda: _scan_swing(persist=True))
                        log.info('Startup catch-up: swing scan complete')
                    except Exception as _e:
                        log.warning('Startup catch-up: swing scan error: %s', _e)
                asyncio.create_task(_startup_swing_scan())
                last_swing_scan_run = _today_iso
                log.info('Startup catch-up: swing scan queued (last scan was %s, not %s)',
                         _scan_date, _today_iso)
        except Exception as _e:
            log.warning('Startup catch-up swing scan check failed: %s', _e)

    while True:
        await asyncio.sleep(SIGNAL_REFRESH_SECS)   # 30s cadence
        try:
            # Stage 1: refresh 1-min bars — hard 45s cap so a slow Schwab response
            # (headers arrive fast, body trickles) can't block the whole cycle.
            try:
                await asyncio.wait_for(refresh_all_1min(), timeout=45)
            except asyncio.TimeoutError:
                log.warning('refresh_all_1min timed out (45s) — skipping this cycle')
            except Exception as e:
                log.warning('refresh_all_1min error: %s', e)
            # Stage 2: compute signals — reads fresh 1-min bars from DB
            try:
                await asyncio.wait_for(refresh_signals(), timeout=90)
            except asyncio.TimeoutError:
                log.warning('refresh_signals timed out (90s) — skipping this cycle')
            except Exception as e:
                log.warning('refresh_signals error: %s', e)

            # Stage 2b: grade OPEN entry_log rows (every 30s, same cadence)
            try:
                await check_entry_log_outcomes()
            except Exception as e:
                log.warning('check_entry_log_outcomes error: %s', e)
            try:
                await refresh_mag10_prices()
            except Exception as e:
                log.warning('refresh_mag10_prices error: %s', e)

            # Incremental 1-min candle update — runs every 60s during RTH
            asyncio.create_task(refresh_holding_candles())

            # refresh_strip_opens() fetches daily candles for MAG10 open prices.
            # rth_open for STRIP_ETFs comes from refresh_signals() live quotes — NOT here
            # (Schwab never returns in-progress daily bars so daily candles can't give today's open).
            if time.time() - last_strip_refresh >= STRIP_REFRESH_SECS:
                try:
                    await refresh_strip_opens()
                except Exception as e:
                    log.warning('refresh_strip_opens error: %s', e)
                last_strip_refresh = time.time()

            # Proactive global-markets refresh — 15 min during US hours, 30 min during
            # Asian session (6 PM – 8 AM ET).  _refresh_global_markets() honours its own
            # DB-cache TTL so this is cheap on most ticks.
            _gm_et_hour = datetime.now(ET).hour
            _gm_ttl     = 1800 if (_gm_et_hour >= 18 or _gm_et_hour < 8) else 900
            if time.time() - last_global_markets >= _gm_ttl:
                try:
                    gm_data = await _refresh_global_markets()
                    if gm_data.get('fx') or gm_data.get('asia'):
                        _GLOBAL_MARKETS_CACHE['data'] = gm_data
                        _GLOBAL_MARKETS_CACHE['ts']   = time.time()
                    last_global_markets = time.time()
                except Exception as e:
                    log.warning('Background global-markets refresh error: %s', e)

            # Refresh ETF holdings once per day
            if time.time() - last_holdings_refresh >= HOLDINGS_REFRESH_SECS:
                asyncio.create_task(refresh_etf_holdings())
                _candle_tickers.clear()   # force ticker list reload after holdings refresh
                last_holdings_refresh = time.time()

            # Re-check active contracts once per hour (catches roll days automatically)
            if time.time() - last_contract_refresh >= CONTRACT_REFRESH_SECS:
                try:
                    await refresh_active_contracts()
                except Exception as e:
                    log.warning('refresh_active_contracts error: %s', e)
                last_contract_refresh = time.time()

            # Refresh YTD once per day
            if time.time() - last_ytd_refresh >= HOLDINGS_REFRESH_SECS:
                asyncio.create_task(refresh_ytd())
                last_ytd_refresh = time.time()

            # Purge 1-min candles older than 4 days once per day
            if time.time() - last_candle_purge >= HOLDINGS_REFRESH_SECS:
                try:
                    deleted = await asyncio.to_thread(delete_old_ticker_candles, 4)
                    if deleted:
                        log.info('Purged %d old 1-min candle rows', deleted)
                except Exception as e:
                    log.warning('Candle purge error: %s', e)
                last_candle_purge = time.time()

            # Daily candle refresh schedule:
            #   4:30 PM ET — incremental (today's close just printed)
            #   2:00 AM ET — full 90-day re-sync (catches any gaps, nightly housekeeping)
            _now_et  = datetime.now(ET)
            _today   = _now_et.date().isoformat()
            _et_hhmm = _now_et.hour * 60 + _now_et.minute

            if _et_hhmm == 16 * 60 + 30 and last_daily_close_run != _today:
                asyncio.create_task(refresh_daily_candles(incremental=True))
                # Full D/W/M candle update for all 646 ticker_universe symbols
                async def _run_daily_update():
                    try:
                        await asyncio.to_thread(
                            lambda: __import__('subprocess').run(
                                ['python3', 'daily_update.py'],
                                cwd=os.path.dirname(__file__),
                            )
                        )
                        log.info('daily_update.py complete (D/W/M for full universe)')
                    except Exception as e:
                        log.warning('daily_update.py error: %s', e)
                asyncio.create_task(_run_daily_update())
                last_daily_close_run = _today

            # 5:30 PM ET on weekdays — validate that all 4:30 PM job steps completed
            if _et_hhmm == 17 * 60 + 30 and last_swing_scan_run != _today and _weekday < 5:
                asyncio.create_task(asyncio.to_thread(_validate_daily_job, _today))
                last_swing_scan_run = _today

            # 5:00 PM ET — compact 1-min bars older than 2 days into 15-min bars
            if _et_hhmm == 17 * 60 and last_1min_agg_run != _today:
                try:
                    result = await asyncio.to_thread(aggregate_1min_to_15min, 2)
                    log.info('1-min → 15-min aggregation: %d buckets written, %d rows deleted',
                             result['aggregated'], result['deleted'])
                except Exception as e:
                    log.warning('1-min aggregation error: %s', e)
                last_1min_agg_run = _today

            # 5:00 AM ET — move yesterday's entry_log rows to archive
            # Runs before the VBH update so the log is clean for the new day.
            if _et_hhmm == 5 * 60 and last_archive_run != _today:
                try:
                    from db import archive_entry_log
                    from datetime import timedelta
                    # Cutoff = start of today ET (midnight) — moves everything from yesterday and older
                    cutoff = datetime.combine(
                        datetime.now(ET).date(), datetime.min.time()
                    ).replace(tzinfo=ET).astimezone(timezone.utc).isoformat()
                    archived = await asyncio.to_thread(archive_entry_log, 'scheduled', cutoff)
                    if archived:
                        log.info('Entry log archive: moved %d rows to entry_log_archive', archived)
                except Exception as e:
                    log.warning('Entry log archive error: %s', e)
                last_archive_run = _today

            # 5:30 AM ET — daily VBH table update (overnight session just closed).
            # Fetches new 30-min Schwab candles for ALL active symbols (futures +
            # stocks/sectors) and stores them in ohlc_30min.  VBH stats are
            # recomputed only for futures — stocks VBH recomputes on Saturday.
            if _et_hhmm == 5 * 60 + 30 and last_vbh_update_run != _today:
                try:
                    from vbh_updater import run_update
                    result = await asyncio.to_thread(
                        run_update, include_stocks=True, vbh_for_stocks=False)
                    log.info('VBH daily update — ok=%s  failed=%s',
                             result['ok'], result['failed'])
                except Exception as e:
                    log.warning('VBH daily update error: %s', e)
                last_vbh_update_run = _today

            # Saturday 8:00 AM ET — weekly VBH recompute for stocks/sectors.
            # Runs once per week after the full week of 30-min bars has been
            # accumulated.  Futures are already up-to-date from the daily run.
            _weekday = datetime.now(ET).weekday()   # 0=Mon … 5=Sat, 6=Sun
            if (_weekday == 5 and _et_hhmm == 8 * 60
                    and last_vbh_stocks_vbh_run != _today):
                try:
                    from vbh_updater import run_update
                    result = await asyncio.to_thread(
                        run_update, include_stocks=True, vbh_for_stocks=True)
                    log.info('VBH weekly stocks recompute — ok=%s  failed=%s',
                             result['ok'], result['failed'])
                except Exception as e:
                    log.warning('VBH weekly stocks recompute error: %s', e)
                last_vbh_stocks_vbh_run = _today

            # 6:00 AM ET on weekdays — GEX daily baseline snapshot (full official OI, pre-market)
            # Weekday check required: OCC only reports OI after session close — Schwab returns
            # zero OI all weekend, so a weekend baseline snapshot is useless noise.
            # Use >= so a server restart after 6 AM still fires the baseline that day.
            #
            # Retry logic: OCC typically publishes by 7-8 AM ET; Schwab may not have loaded
            # overnight OI at exactly 6 AM. _refresh_gex_indices returns False when all GEX
            # is zero (OI not available yet) — retry every 5 min until real data arrives.
            _gex_baseline_retry_ok = (time.time() - last_gex_baseline_ts) >= 300
            if _et_hhmm >= 6 * 60 and last_gex_baseline_run != _today and _weekday < 5 and _gex_baseline_retry_ok:
                try:
                    got_oi = await asyncio.to_thread(_refresh_gex_indices, True)
                    last_gex_baseline_ts = time.time()
                    if got_oi:
                        last_gex_baseline_run = _today   # mark done only when real OI arrives
                        log.info('GEX daily baseline saved for %s (real OI confirmed)', GEX_INDEX_SYMBOLS)
                    else:
                        log.info('GEX baseline: OI not ready yet — will retry in 5 min')
                except Exception as e:
                    last_gex_baseline_ts = time.time()
                    log.warning('GEX baseline error (will retry in 5 min): %s', e)

            # 5:30 PM ET on weekdays — GEX daily baseline for tracked stock symbols
            # Runs AFTER market close so Schwab still has settled OI for the day.
            # Index symbols (SPX/NDX/RUT) are handled by the 6 AM baseline above.
            if _et_hhmm == 17 * 60 + 30 and last_gex_stocks_run != _today and _weekday < 5:
                try:
                    await asyncio.to_thread(_refresh_gex_stocks)
                    log.info('GEX stock baseline snapshots complete')
                except Exception as e:
                    log.warning('GEX stock snapshot error: %s', e)
                last_gex_stocks_run = _today

            # 6:00 AM ET — refresh stock fundamental profiles (yfinance)
            if _et_hhmm == 6 * 60 and last_profiles_run != _today:
                asyncio.create_task(refresh_stock_profiles())
                last_profiles_run = _today

            # Every 15 min during RTH (9:30 AM – 4:00 PM ET, weekdays only) — GEX intraday estimate
            # Uses live Schwab chain; volume proxies OI changes — approximate but directional.
            # Weekday guard required: Schwab zeroes all OI on weekends; saving those snapshots
            # pollutes the DB and masks the last valid baseline on Monday morning.
            _is_rth = _weekday < 5 and 9 * 60 + 30 <= _et_hhmm < 16 * 60
            if _is_rth and (time.time() - last_gex_intraday_ts) >= GEX_INTRADAY_SECS:
                try:
                    await asyncio.to_thread(_refresh_gex_indices, False)
                except Exception as e:
                    log.warning('GEX intraday refresh error: %s', e)
                try:
                    await asyncio.to_thread(_refresh_gex_intraday_stocks)
                except Exception as e:
                    log.warning('GEX intraday stocks error: %s', e)
                last_gex_intraday_ts = time.time()

            # Purge old intraday GEX rows (keep 5 days) — runs with daily candle purge
            if time.time() - last_candle_purge < 5:   # candle purge just ran
                try:
                    from db import purge_old_gex_snapshots
                    purged = await asyncio.to_thread(purge_old_gex_snapshots, 5)
                    if purged:
                        log.info('Purged %d old GEX intraday rows', purged)
                except Exception:
                    pass

            if time.time() - last_daily_refresh >= 86400:   # full re-sync once per day
                asyncio.create_task(refresh_daily_candles(incremental=False))
                last_daily_refresh = time.time()

            if state['last_stats_update']:
                age_h = (datetime.now(ET) - datetime.fromisoformat(
                    state['last_stats_update'])).total_seconds() / 3600
                if age_h >= STATS_REFRESH_HOURS:
                    await compute_all_stats()

        except Exception as _loop_exc:
            state['last_loop_error'] = f'{type(_loop_exc).__name__}: {_loop_exc}'
            log.error('background_loop cycle error (will retry next tick): %s', _loop_exc, exc_info=True)


# ── Stock Profiles — yfinance fundamentals, refreshed daily ──────────────────

async def refresh_stock_profiles():
    """Fetch fundamental + earnings data for all stock symbols via yfinance.

    Runs at startup and daily at 6:00 AM ET.  8 concurrent workers so 109 stocks
    complete in ~30s instead of 5+ minutes.

    Per ticker:
      - info:            market cap, short float, beta, analyst rating/target, revenue TTM
      - earnings_dates:  last 8 quarters EPS estimate vs actual, surprise %
      - history(2y):     daily OHLC — used to compute overnight post-earnings move
                         (prior RTH close → next day open)
      - news:            3 most recent headlines
    """
    import yfinance as yf
    import pandas as pd

    stock_syms = [
        s for s in state['symbols']
        if not s['ticker'].startswith('/')
        and s['ticker'] not in SECTOR_TICKERS
    ]
    if not stock_syms:
        log.info('stock_profiles: no stock symbols found')
        return

    log.info('stock_profiles: refreshing %d symbols (8 concurrent)', len(stock_syms))

    REC_MAP = {
        'strong_buy': 'Strong Buy', 'buy': 'Buy',
        'hold': 'Hold', 'sell': 'Sell', 'strong_sell': 'Strong Sell',
    }

    def _fetch_one(ticker: str) -> dict | None:
        """All yfinance I/O for one ticker — runs in thread pool."""
        try:
            yt   = yf.Ticker(ticker)
            info = yt.info

            # ── Earnings history — last 8 reported quarters ────────────────
            earnings_history: list[dict] = []
            try:
                edf = yt.earnings_dates        # index=date, cols: EPS Estimate / Reported EPS / Surprise(%)
                if edf is not None and len(edf) > 0:
                    # 2-year daily bars for overnight-move calculation
                    hist = yt.history(period='2y', interval='1d')
                    hist.index = hist.index.normalize()

                    for date, row in edf.iterrows():
                        eps_actual = row.get('Reported EPS')
                        if eps_actual is None or pd.isna(eps_actual):
                            continue          # future quarter — no actual yet
                        eps_actual    = round(float(eps_actual), 2)
                        eps_estimate  = row.get('EPS Estimate')
                        eps_estimate  = round(float(eps_estimate), 2) if (eps_estimate is not None and not pd.isna(eps_estimate)) else None
                        surprise_pct  = row.get('Surprise(%)')
                        surprise_pct  = round(float(surprise_pct), 1) if (surprise_pct is not None and not pd.isna(surprise_pct)) else None
                        result        = ('BEAT' if eps_actual > (eps_estimate or 0) else 'MISS') if eps_estimate is not None else None

                        # Overnight reaction: prior close → next trading day open
                        move_pct = None
                        try:
                            date_ts   = pd.Timestamp(date.date())
                            prior_row = hist[hist.index < date_ts]
                            next_row  = hist[hist.index > date_ts]
                            if len(prior_row) > 0 and len(next_row) > 0:
                                prior_close = float(prior_row.iloc[-1]['Close'])
                                next_open   = float(next_row.iloc[0]['Open'])
                                if prior_close > 0:
                                    move_pct = round((next_open - prior_close) / prior_close * 100, 1)
                        except Exception:
                            pass

                        _m = date.month
                        quarter = f"Q{(_m-1)//3+1} {date.year}"
                        earnings_history.append({
                            'date':         date.strftime('%b %Y'),
                            'report_date':  date.date().isoformat(),
                            'quarter':      quarter,
                            'eps_estimate': eps_estimate,
                            'eps_actual':   eps_actual,
                            'surprise_pct': surprise_pct,
                            'result':       result,
                            'move_pct':     move_pct,
                        })
                        if len(earnings_history) >= 8:
                            break
            except Exception as e:
                log.debug('earnings_dates %s: %s', ticker, e)

            # ── Beat stats ─────────────────────────────────────────────────
            beat_count  = sum(1 for h in earnings_history if h['result'] == 'BEAT')
            beat_streak = 0
            for h in earnings_history:
                if h['result'] == 'BEAT': beat_streak += 1
                else: break
            surprises   = [h['surprise_pct'] for h in earnings_history if h['surprise_pct'] is not None]
            moves       = [abs(h['move_pct'])  for h in earnings_history if h['move_pct']  is not None]
            avg_surprise = round(sum(surprises) / len(surprises), 1) if surprises else None
            avg_move     = round(sum(moves)     / len(moves),     1) if moves     else None

            # Most recent completed quarter EPS (Overview hero row)
            last_q = earnings_history[0] if earnings_history else {}

            # ── Next earnings date ─────────────────────────────────────────
            next_earnings_date = None
            raw_dates = info.get('earningsDate')
            if raw_dates:
                import datetime as _dt
                ts  = raw_dates[0] if isinstance(raw_dates, list) else raw_dates
                ned = _dt.datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
                next_earnings_date = ned.isoformat()

            # ── News (3 most recent headlines) ─────────────────────────────
            news: list[dict] = []
            try:
                raw_news = yt.news or []
                for n in raw_news[:5]:   # scan up to 5, keep first 3 non-empty
                    title     = (n.get('title') or n.get('Title') or '')[:120]
                    publisher = (n.get('publisher') or n.get('Publisher') or
                                 (n.get('source') or {}).get('name') or '')
                    pub_time  = (n.get('providerPublishTime') or n.get('publishedAt') or
                                 n.get('time') or 0)
                    if title:
                        news.append({'title': title, 'publisher': publisher, 'published_at': pub_time})
                    if len(news) >= 3:
                        break
            except Exception:
                pass

            # Logo is constructed client-side from the ticker via logo.dev —
            # no URL to fetch or store here.

            # ── Analyst ────────────────────────────────────────────────────
            rec_key = (info.get('recommendationKey') or '').lower().replace(' ', '_')

            return {
                'ticker':               ticker,
                'company_name':         info.get('longName') or info.get('shortName') or ticker,
                'sector':               info.get('sector') or '',
                'industry':             info.get('industry') or '',
                'exchange':             info.get('exchange') or '',
                'market_cap':           info.get('marketCap'),
                'short_float':          info.get('shortPercentOfFloat'),
                'beta':                 info.get('beta'),
                'revenue_ttm':          info.get('totalRevenue'),
                'week_52_high':         info.get('fiftyTwoWeekHigh'),
                'week_52_low':          info.get('fiftyTwoWeekLow'),
                'analyst_rating':       REC_MAP.get(rec_key, ''),
                'analyst_count':        info.get('numberOfAnalystOpinions'),
                'target_price':         info.get('targetMeanPrice'),
                # Most recent quarter EPS
                'last_eps_actual':      last_q.get('eps_actual'),
                'last_eps_estimate':    last_q.get('eps_estimate'),
                'last_eps_surprise_pct': last_q.get('surprise_pct'),
                'last_eps_result':      last_q.get('result'),
                # Earnings
                'next_earnings_date':   next_earnings_date,
                'earnings_history':     earnings_history,
                'beat_count':           beat_count,
                'beat_streak':          beat_streak,
                'avg_surprise_pct':     avg_surprise,
                'avg_move_pct':         avg_move,
                # News
                'news':                 news,
                'refreshed_at':         datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            log.warning('stock_profile %s: %s', ticker, exc)
            return None

    # ── 8-concurrent fetch ──────────────────────────────────────────────────
    sem = asyncio.Semaphore(8)

    async def _bounded(ticker: str):
        async with sem:
            return await asyncio.to_thread(_fetch_one, ticker)

    results = await asyncio.gather(*[_bounded(s['ticker']) for s in stock_syms])

    fresh = {r['ticker']: r for r in results if r}
    _STOCK_PROFILES.update(fresh)
    log.info('stock_profiles: %d / %d succeeded', len(fresh), len(stock_syms))

    # Persist to Supabase.  Try full upsert including JSONB fields (earnings_history,
    # news) first; if the table doesn't have those columns yet, fall back to scalars only.
    _JSON_FIELDS = {'earnings_history', 'news'}
    try:
        from db import get_db as _get_db
        rows = [dict(p) for p in fresh.values()]
        if rows:
            try:
                _get_db().table('stock_profiles').upsert(rows).execute()
                log.info('stock_profiles: upserted %d rows (full) to Supabase', len(rows))
            except Exception:
                # JSONB columns may not exist — fall back to scalar fields only
                scalar_rows = [{k: v for k, v in p.items() if k not in _JSON_FIELDS} for p in fresh.values()]
                _get_db().table('stock_profiles').upsert(scalar_rows).execute()
                log.info('stock_profiles: upserted %d rows (scalar only) to Supabase', len(rows))
    except Exception as exc:
        log.debug('stock_profiles: Supabase upsert skipped — %s', exc)

    # ── Upsert into normalized stock_earnings table ───────────────────────────
    try:
        from db import get_db as _get_db
        earning_rows = []
        for profile in fresh.values():
            ticker = profile['ticker']
            for h in (profile.get('earnings_history') or []):
                earning_rows.append({
                    'ticker':       ticker,
                    'quarter':      h['quarter'],
                    'report_date':  h.get('report_date'),
                    'eps_estimate': h.get('eps_estimate'),
                    'eps_actual':   h.get('eps_actual'),
                    'surprise_pct': h.get('surprise_pct'),
                    'result':       h.get('result'),
                    'move_pct':     h.get('move_pct'),
                })
        if earning_rows:
            _get_db().table('stock_earnings').upsert(
                earning_rows, on_conflict='ticker,quarter'
            ).execute()
            log.info('stock_earnings: upserted %d rows', len(earning_rows))
    except Exception as exc:
        log.debug('stock_earnings: upsert skipped — %s', exc)


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_loop())
    yield
    task.cancel()


app = FastAPI(title='domytrade API', lifespan=lifespan)

# ── WebSocket connection manager ───────────────────────────────────────────────
from fastapi import WebSocket, WebSocketDisconnect as _WSDisconnect

class _WSManager:
    """Manages all active /ws/signals connections and broadcasts."""
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)
        log.info('WS client connected  (total: %d)', len(self.clients))

    def disconnect(self, ws: WebSocket):
        self.clients = [c for c in self.clients if c is not ws]
        log.info('WS client disconnected (total: %d)', len(self.clients))

    async def broadcast(self, payload: dict):
        """Send payload to all connected clients; drop dead connections."""
        dead = []
        for c in self.clients:
            try:
                await c.send_json(payload)
            except Exception:
                dead.append(c)
        for c in dead:
            self.clients = [x for x in self.clients if x is not c]

ws_manager = _WSManager()

app.add_middleware(CORSMiddleware,
    allow_origins=[
        'http://localhost:3000',
        'https://domytrade.app',
        'https://www.domytrade.app',
    ],
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Global exception handler — ensures CORS headers survive unhandled errors ──
# Without this, Railway returns a plain-text 500 with no CORS headers,
# which the browser sees as "TypeError: Failed to fetch" instead of a real error.
from fastapi.responses import JSONResponse as _JSONResponse
from fastapi.requests import Request as _Request
@app.exception_handler(Exception)
async def _unhandled_exception_handler(_req: _Request, exc: Exception):
    log.exception('Unhandled exception in %s', _req.url.path)
    return _JSONResponse(
        status_code=500,
        content={'error': 'server_error', 'message': str(exc)[:200]},
    )


@app.get('/api/signals')
def get_signals(model: str = Query('all'), side: str = Query('all')):
    rows = state['signals']
    if model != 'all':
        rows = [r for r in rows if r['model'].lower() == model.lower()]
    if side != 'all':
        rows = [r for r in rows if r['side'].lower() == side.lower()]
    return {
        'signals'     : rows,
        'count'       : len(rows),
        'longs'       : sum(1 for r in rows if r['side'] == 'LONG'),
        'shorts'      : sum(1 for r in rows if r['side'] == 'SHORT'),
        'last_updated': state['last_signal_update'],
        'last_stats'  : state['last_stats_update'],
        'status'      : state['status'],
    }


@app.websocket('/ws/signals')
async def ws_signals(ws: WebSocket):
    """WebSocket endpoint — pushes signal updates in real time.

    On connect: immediately sends the current snapshot so the client has
    data before the next 30-second backend refresh cycle.

    Message types sent to client:
      { type: 'snapshot', signals: [...], last_updated: '...' }
      { type: 'update',   signals: [...], last_updated: '...' }

    Client can send 'ping' → server replies with 'pong' (keepalive).
    """
    await ws_manager.connect(ws)
    # Immediate snapshot so the client doesn't wait up to 30s for first data
    await ws.send_json({
        'type':         'snapshot',
        'signals':      state['signals'],
        'last_updated': state['last_signal_update'],
    })
    try:
        while True:
            msg = await ws.receive_text()
            if msg == 'ping':
                await ws.send_json({'type': 'pong'})
    except _WSDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


@app.get('/api/debug/loop')
def debug_loop():
    """Quick health probe for the background loop — shows last update age."""
    import time as _time
    last = state.get('last_signal_update')
    age_s = None
    if last:
        try:
            age_s = round((datetime.now(ET) - datetime.fromisoformat(last)).total_seconds())
        except Exception:
            pass
    return {
        'status'         : state['status'],
        'last_updated'   : last,
        'age_seconds'    : age_s,
        'signal_count'   : len(state['signals']),
        'last_stats'     : state['last_stats_update'],
        'token_state'    : state.get('token_state', 'unknown'),
        'last_loop_error': state.get('last_loop_error'),
    }


@app.get('/api/health')
def health():
    return {
        'status' : state['status'],
        'signals': len(state['signals']),
        'symbols': len(state['symbols']),
    }


@app.get('/api/token-status')
def token_status():
    """Return Schwab refresh-token age so the frontend can warn before expiry.

    Schwab refresh tokens are valid for ~7 days.  We store the issued_at
    timestamp in app_cache alongside the token so we can compute how many
    days remain without making any Schwab API calls.
    """
    from db import cache_get
    SCHWAB_REFRESH_TTL_DAYS = 7

    try:
        cached = cache_get('schwab_refresh_token') or {}
        issued_at_str = cached.get('issued_at')
        if not issued_at_str:
            return {
                'status':   'unknown',
                'message':  'Token issue date not recorded — renew to start tracking.',
                'age_days': None,
                'days_remaining': None,
            }
        issued_at   = datetime.fromisoformat(issued_at_str)
        age_days    = (datetime.now(timezone.utc) - issued_at).total_seconds() / 86400
        days_left   = SCHWAB_REFRESH_TTL_DAYS - age_days

        if days_left <= 0:
            status  = 'expired'
            message = 'Schwab token EXPIRED — run renew_schwab_token.py immediately.'
        elif days_left <= 1.5:
            status  = 'critical'
            message = f'Token expires in {days_left:.1f} days — renew today.'
        elif days_left <= 3:
            status  = 'warning'
            message = f'Token expires in {days_left:.1f} days — renew soon.'
        else:
            status  = 'ok'
            message = f'Token valid — {days_left:.1f} days remaining.'

        return {
            'status':         status,
            'message':        message,
            'age_days':       round(age_days, 1),
            'days_remaining': round(days_left, 1),
        }
    except Exception as e:
        return {'status': 'unknown', 'message': str(e), 'age_days': None, 'days_remaining': None}


@app.get('/api/debug/contracts')
def debug_contracts():
    """Expose discovered active contracts and computed front months for verification."""
    futures = [s for s in state['symbols'] if s['ticker'].startswith('/')]
    result = []
    for sym in futures:
        tick = sym['ticker']
        active  = state['active_contracts'].get(tick)
        computed = front_month_code(tick)
        result.append({
            'ticker'          : tick,
            'schwab_symbol'   : sym['schwab_symbol'],
            'active_contract' : active,
            'computed_front'  : computed,
            'using'           : active or computed,
        })
    return {'contracts': result, 'total': len(result)}


MARKET_TICKERS = {'/ES', '/NQ', '/YM', '/RTY'}


@app.get('/api/market-bias')
def get_market_bias():
    result = []
    for sym in state['symbols']:
        if sym['ticker'] not in MARKET_TICKERS:
            continue
        sid  = sym['id']
        bias = state['market_bias'].get(sid, {
            'bias': 'NEUTRAL', 'pts': 0.0, 'rth_open': 0.0, 'prev_close': 0.0
        })
        result.append({
            'symbol'    : sym['ticker'],
            'bias'      : bias['bias'],
            'pts'       : bias['pts'],
            'rth_open'  : bias['rth_open'],
            'prev_close': bias['prev_close'],
            'vwap'      : bias.get('vwap'),
            'poc'       : bias.get('poc'),
            'gap'       : bias.get('gap'),
        })
    # Fixed display order
    order = ['/ES', '/NQ', '/YM', '/RTY']
    result.sort(key=lambda r: order.index(r['symbol']) if r['symbol'] in order else 99)
    return {
        'markets'   : result,
        'volatility': state['volatility'],
    }


@app.get('/api/industries')
def get_industries():
    """% change from RTH open for the 11 SPDR sector ETFs — powers the Industries strip.
    rth_open = Schwab quote open field (today's 9:30 ET open), set by refresh_signals()
    every 60s during RTH. Schwab price-history API never returns an in-progress daily bar
    so refresh_strip_opens() cannot be used as the source — live quote open is the only
    reliable intraday source.

    current price:
      - During RTH (9:30–16:00 ET weekdays): live last_price from Schwab quote (60s refresh)
      - Outside RTH: last_price (today's close or AH) with prev_close fallback"""
    ticker_map = {s['ticker']: s for s in state['symbols']}
    result = []
    for etf in STRIP_ETFS:
        tick = etf['ticker']
        sym  = ticker_map.get(tick)
        if not sym:
            continue
        sid        = sym['id']
        # strip_prev_close: refreshed every 5 min from daily candles — always the most
        # recent completed RTH session's close, independent of the 24h stats cycle.
        # Falls back to prev_close (set at startup) if strip candles haven't loaded yet.
        prev_close = (state['strip_prev_close'].get(sid)
                      or state['prev_close'].get(sid, 0))
        # current price: live last during RTH, today's candle close after 4 PM.
        current    = state['last_price'].get(sid, 0) or prev_close
        # Standard daily % change: vs yesterday's close (includes the overnight gap).
        # This matches TOS / Bloomberg convention and gives the full day's move.
        pct        = round((current - prev_close) / prev_close * 100, 2) if (prev_close and current) else 0.0
        result.append({'symbol': tick, 'name': etf['name'], 'weight': etf.get('weight'), 'pct': pct})

    # MAG10 index — same formula as TOS ThinkScript:
    #   idx = Σ(price / div × weight)
    #   pct = (idx_now - idx_prev) / idx_prev × 100
    idx_now  = 0.0
    idx_prev = 0.0
    mag10_ok = True
    for comp in MAG10_COMPONENTS:
        t          = comp['ticker']
        last       = state['mag10_last'].get(t, 0)
        prev_close = state['mag10_prev_close'].get(t, 0)
        if not last or not prev_close:
            mag10_ok = False
            break
        idx_now  += last       / comp['div'] * comp['weight']
        idx_prev += prev_close / comp['div'] * comp['weight']

    mag10_pct = round((idx_now - idx_prev) / idx_prev * 100, 2) if (mag10_ok and idx_prev) else 0.0
    result.append({'symbol': 'MAG10', 'name': 'MAG10', 'pct': mag10_pct})

    return {'industries': result}


@app.get('/api/sector-ytd')
def get_sector_ytd():
    """Return cached YTD % for all SECTOR_TICKERS + SPY. Served instantly from state."""
    return state['ytd']


@app.get('/api/candles')
async def get_multi_candles(symbols: str = Query(...), days: int = Query(3)):
    """Return stored 1-min candles from DB for up to 25 tickers.
    Reads from ticker_candles_1min first, falls back to live Schwab if empty.
    Returns {ticker: [{t, o, h, l, c, v}, ...]}."""
    tickers = [t.strip().upper() for t in symbols.split(',') if t.strip()][:25]
    if not tickers:
        return {}

    async def fetch_one(ticker: str) -> tuple[str, list]:
        try:
            # 1. Try DB first
            rows = await asyncio.to_thread(get_ticker_candles, ticker, days)
            if rows:
                return ticker, [
                    {'t': int(datetime.fromisoformat(r['bar_time']).timestamp() * 1000),
                     'o': float(r['open']), 'h': float(r['high']),
                     'l': float(r['low']),  'c': float(r['close']),
                     'v': r.get('volume', 0)}
                    for r in rows
                ]
            # 2. Fallback — live Schwab (first run before backfill completes)
            raw = await asyncio.to_thread(get_candles, ticker, days, 1)
            if not raw:
                return ticker, []
            return ticker, [
                {'t': c['datetime'], 'o': c['open'], 'h': c['high'],
                 'l': c['low'],      'c': c['close'], 'v': c.get('volume', 0)}
                for c in raw
            ]
        except Exception as e:
            log.warning('candles/%s: %s', ticker, e)
            return ticker, []

    sem     = asyncio.Semaphore(8)
    async def bounded(t):
        async with sem:
            return await fetch_one(t)

    results = await asyncio.gather(*[bounded(t) for t in tickers])
    return dict(results)


# ── Tick sizes for VPOC price-level granularity ───────────────────────────────
_TICK = {
    '/ES': 0.25, '/NQ': 0.25, '/YM': 1.0, '/RTY': 0.10,
    '/CL': 0.01, '/NG': 0.001, '/GC': 0.10, '/SI': 0.005,
    '/HG': 0.0005, '/ZB': 0.03125, '/ZN': 0.015625,
    '/ZC': 0.25, '/ZS': 0.25, '/RB': 0.0001, '/PL': 0.10,
    '/BTC': 5.0,
}

def _tick_for(symbol: str) -> float:
    for prefix, tick in _TICK.items():
        if symbol.startswith(prefix):
            return tick
    return 0.01   # equities default


def _compute_vwap(bars: list[dict]) -> float | None:
    """VWAP = Σ(typical_price × volume) / Σ(volume).  Typical price = (H+L+C)/3."""
    total_vol    = sum(b.get('volume', 0) for b in bars)
    if not total_vol:
        return None
    total_tp_vol = sum(((b['high'] + b['low'] + b['close']) / 3) * b.get('volume', 0) for b in bars)
    return round(total_tp_vol / total_vol, 2)


def _compute_vpoc(bars: list[dict], tick: float) -> float | None:
    """Uniform volume distribution across each bar's H-L range → price with max volume."""
    from collections import defaultdict
    vol_map: dict[float, float] = defaultdict(float)
    for b in bars:
        hi, lo, vol = b['high'], b['low'], b.get('volume', 0)
        if not vol:
            continue
        if hi == lo:
            vol_map[round(round(lo / tick) * tick, 6)] += vol
            continue
        n = max(1, round((hi - lo) / tick) + 1)
        vol_each = vol / n
        p = lo
        for _ in range(n):
            vol_map[round(round(p / tick) * tick, 6)] += vol_each
            p += tick
    return max(vol_map, key=vol_map.get) if vol_map else None


@app.get('/api/levels/{symbol:path}')
async def get_levels(symbol: str):
    """Key price levels for a futures (or equity) symbol.

    Computed automatically:
      session_vpoc    — prior RTH session VPOC
      overnight_vpoc  — overnight session VPOC (pre-9:30 ET)
      mcvpoc_3day     — 3-session composite VPOC
      daily_pivot     — (prev H+L+C)/3
      weekly_pivot    — (prev-week H+L+C)/3
      weekly_open     — first bar of current week
      ath_intraday    — rolling 30-day intraday high
      swing_high      — 10-day fractal high
      swing_low       — 10-day fractal low
      prev_high/low/close — prior session OHLC reference
    """
    symbol = symbol.upper()

    # Serve from cache if fresh
    _cached = _LEVELS_CACHE.get(symbol)
    if _cached and (datetime.now(ET) - _cached['ts']).total_seconds() < _LEVELS_CACHE_TTL:
        return _cached['data']

    tick   = _tick_for(symbol)
    now_et = datetime.now(ET)
    today  = now_et.date()

    # Fetch 5 days of 1-min bars (extended hours) + 30 daily bars concurrently
    raw_1min, daily = await asyncio.gather(
        asyncio.to_thread(get_candles, symbol, 5, 1),
        asyncio.to_thread(get_daily_candles, symbol, 30),
    )

    # ── Classify 1-min bars by session ─────────────────────────────────────
    # rth_sessions:  {date: [bars]}  — 09:30–16:00 ET
    # on_sessions:   {date: [bars]}  — midnight→9:29 ET + Sunday evening (CME overnight)
    rth_by_date: dict = {}
    on_by_date:  dict = {}

    for c in raw_1min:
        dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        d     = dt.date()
        t_min = dt.hour * 60 + dt.minute
        wday  = dt.weekday()   # 0=Mon … 6=Sun

        is_rth      = wday < 5 and (9 * 60 + 30) <= t_min <= 16 * 60
        is_on_wkday = wday < 5 and t_min < (9 * 60 + 30)
        is_evening  = wday < 4 and t_min >= 18 * 60   # Mon–Thu 6 PM+ → next day's overnight
        is_sunday   = wday == 6                         # CME reopens Sun 6 PM ET → Monday overnight

        if is_rth:
            rth_by_date.setdefault(d, []).append(c)
        elif is_on_wkday:
            on_by_date.setdefault(d, []).append(c)
        elif is_evening or is_sunday:
            on_by_date.setdefault(d + timedelta(days=1), []).append(c)

    sorted_rth_dates = sorted(rth_by_date.keys(), reverse=True)

    # Prior RTH VPOC / VAH / VAL — last completed RTH session (9:30–16:00)
    prior_rth_vpoc = None
    prior_rth_vah  = None
    prior_rth_val  = None
    prior_rth_tpo_vpoc = prior_rth_tpo_vah = prior_rth_tpo_val = None
    prior_rth_dates_asc = [d for d in sorted_rth_dates if d < today]
    if prior_rth_dates_asc:
        prior_rth_bars = rth_by_date.get(prior_rth_dates_asc[0], [])
        _prior_va      = _compute_value_area(prior_rth_bars, tick)
        prior_rth_vpoc = _prior_va['poc']
        prior_rth_vah  = _prior_va['vah']
        prior_rth_val  = _prior_va['val']
        # Dalton TPO (exact — no volume distribution assumption)
        _prior_tpo     = _compute_tpo_value_area(prior_rth_bars, tick)
        prior_rth_tpo_vpoc = _prior_tpo['poc']
        prior_rth_tpo_vah  = _prior_tpo['vah']
        prior_rth_tpo_val  = _prior_tpo['val']

    # Overnight VPOC / VAH / VAL — today's pre-market session (incl. Sunday night for Monday)
    overnight_vpoc = overnight_vah = overnight_val = None
    overnight_tpo_vpoc = overnight_tpo_vah = overnight_tpo_val = None
    _on_bars = on_by_date.get(today, [])
    if _on_bars:
        _on_va = _compute_value_area(_on_bars, tick)
        overnight_vpoc = _on_va['poc']
        overnight_vah  = _on_va['vah']
        overnight_val  = _on_va['val']
        _on_tpo        = _compute_tpo_value_area(_on_bars, tick)
        overnight_tpo_vpoc = _on_tpo['poc']
        overnight_tpo_vah  = _on_tpo['vah']
        overnight_tpo_val  = _on_tpo['val']
    if not overnight_vpoc:
        # Fallback: most recent overnight with data
        for d in sorted(on_by_date.keys(), reverse=True):
            _fb_va = _compute_value_area(on_by_date[d], tick)
            if _fb_va['poc']:
                overnight_vpoc = _fb_va['poc']
                overnight_vah  = _fb_va['vah']
                overnight_val  = _fb_va['val']
                _fb_tpo        = _compute_tpo_value_area(on_by_date[d], tick)
                overnight_tpo_vpoc = _fb_tpo['poc']
                overnight_tpo_vah  = _fb_tpo['vah']
                overnight_tpo_val  = _fb_tpo['val']
                break

    # Developing RTH — today's live RTH session (null before 9:30 or on weekends)
    developing_vpoc = developing_vah = developing_val = None
    developing_tpo_vpoc = developing_tpo_vah = developing_tpo_val = None
    _dev_bars = rth_by_date.get(today, [])
    if _dev_bars:
        _dev_va         = _compute_value_area(_dev_bars, tick)
        developing_vpoc = _dev_va['poc']
        developing_vah  = _dev_va['vah']
        developing_val  = _dev_va['val']
        _dev_tpo            = _compute_tpo_value_area(_dev_bars, tick)
        developing_tpo_vpoc = _dev_tpo['poc']
        developing_tpo_vah  = _dev_tpo['vah']
        developing_tpo_val  = _dev_tpo['val']

    # MCVPOC 3-day — composite of the 3 most recent completed RTH sessions
    mc3: list[dict] = []
    for d in prior_rth_dates_asc[:3]:
        mc3.extend(rth_by_date[d])
    mcvpoc_3day = _compute_vpoc(mc3, tick) if mc3 else None

    # ── Daily candle derived levels ─────────────────────────────────────────
    daily_pivot = weekly_pivot = weekly_open = None
    ath_intraday = swing_high = swing_low = None
    prev_high = prev_low = prev_close = None

    def _bar_date(c):
        return datetime.fromtimestamp(
            c['datetime'] / 1000, tz=timezone.utc
        ).astimezone(ET).date()

    if daily:
        # Previous completed session — daily candle gives extended-hours OHLC.
        # We override prev_high/prev_low/prev_close/daily_pivot below with 1-min
        # RTH bars so we always use true RTH values (9:30–4:00 PM), not 5 PM settle.
        prior_daily = [c for c in daily if _bar_date(c) < today]
        if prior_daily:
            prev        = prior_daily[-1]
            prev_high   = prev['high']
            prev_low    = prev['low']
            prev_close  = prev['close']
            daily_pivot = round((prev['high'] + prev['low'] + prev['close']) / 3, 4)

    # ── Override prev_high / prev_low / prev_close / daily_pivot from 1-min RTH ──
    # Daily Schwab candles for futures include extended hours (close = ~5 PM settle).
    # Use the last 1-min RTH bar for a true 4:00 PM close.
    if prior_rth_dates_asc:
        _prior_bars_1m = rth_by_date.get(prior_rth_dates_asc[0], [])
        if _prior_bars_1m:
            _sorted_1m  = sorted(_prior_bars_1m, key=lambda b: b['datetime'])
            prev_high   = max(b['high'] for b in _prior_bars_1m)
            prev_low    = min(b['low']  for b in _prior_bars_1m)
            prev_close  = _sorted_1m[-1]['close']   # last bar = 4:00 PM bar close
            daily_pivot = round((prev_high + prev_low + prev_close) / 3, 4)

    if daily:

        # ATH intraday (rolling 30-day + today's session so far)
        ath_intraday = max(c['high'] for c in daily)
        # Today's intraday high may not be in the daily bars yet (holiday / partial day)
        # — extend with today's 1-min bars so the ATH updates in real time
        today_bars_all = rth_by_date.get(today, []) + on_by_date.get(today, [])
        if today_bars_all:
            today_high   = max(c['high'] for c in today_bars_all)
            ath_intraday = max(ath_intraday, today_high)

        monday_this = today - timedelta(days=today.weekday())
        monday_prev = monday_this - timedelta(days=7)
        friday_prev = monday_prev + timedelta(days=4)

        # Weekly open — first daily bar of current week
        this_week_daily = [c for c in daily if _bar_date(c) >= monday_this]
        if this_week_daily:
            weekly_open = this_week_daily[0]['open']

        # FIX swing high/low: use 1-min RTH session data (not daily candles which include
        # extended-hours H/L even with needExtendedHoursData=false for futures)
        sorted_prior_rth = sorted([d for d in rth_by_date if d < today])
        rth_sess_ohlc = []
        for sd in sorted_prior_rth:
            sb = rth_by_date[sd]
            rth_sess_ohlc.append({
                'high': max(b['high'] for b in sb),
                'low' : min(b['low']  for b in sb),
            })
        recent_sess = rth_sess_ohlc[-15:] if len(rth_sess_ohlc) >= 3 else rth_sess_ohlc
        _sh = _sl = None
        for i in range(len(recent_sess) - 2, 0, -1):
            if _sh is None and recent_sess[i]['high'] > recent_sess[i-1]['high'] and recent_sess[i]['high'] > recent_sess[i+1]['high']:
                _sh = recent_sess[i]['high']
            if _sl is None and recent_sess[i]['low'] < recent_sess[i-1]['low'] and recent_sess[i]['low'] < recent_sess[i+1]['low']:
                _sl = recent_sess[i]['low']
            if _sh is not None and _sl is not None:
                break
        swing_high = _sh or (max(s['high'] for s in recent_sess) if recent_sess else None)
        swing_low  = _sl or (min(s['low']  for s in recent_sess) if recent_sess else None)

        # On holidays / overnight sessions the current price may have moved above the
        # last RTH swing high — update swing_high to today's session high so it stays
        # relevant as the most recent structure high.
        today_on_bars = on_by_date.get(today, []) + rth_by_date.get(today, [])
        if today_on_bars:
            today_session_high = max(c['high'] for c in today_on_bars)
            if swing_high is None or today_session_high > swing_high:
                swing_high = today_session_high

        # Weekly pivot: prefer 1-min extended-hours data for true H/L of prior week
        # (daily RTH candles miss overnight highs — ~5 pt difference).
        # Falls back to daily candles when 1-min history doesn't cover the prior week
        # (e.g. 5-day fetch window on weekends).
        prev_week_1min = [c for c in raw_1min if monday_prev <= _bar_date(c) <= friday_prev]
        if prev_week_1min:
            wh = max(c['high'] for c in prev_week_1min)
            wl = min(c['low']  for c in prev_week_1min)
            prev_week_rth_dates = sorted([d for d in rth_by_date if monday_prev <= d <= friday_prev])
            if prev_week_rth_dates:
                wc = rth_by_date[prev_week_rth_dates[-1]][-1]['close']
                weekly_pivot = round((wh + wl + wc) / 3, 4)
        else:
            # Fallback: use daily candles (RTH only — slightly less accurate but always available)
            prev_week_daily = [c for c in daily if monday_prev <= _bar_date(c) < monday_this]
            if prev_week_daily:
                wh = max(c['high']  for c in prev_week_daily)
                wl = min(c['low']   for c in prev_week_daily)
                wc = prev_week_daily[-1]['close']
                weekly_pivot = round((wh + wl + wc) / 3, 4)

    # VWAP — current session if live, then overnight/holiday session, then prior RTH
    today_bars = rth_by_date.get(today, [])
    if today_bars:
        vwap = _compute_vwap(today_bars)
    else:
        # Holiday / pre-market: compute from overnight session bars (reflects live trading).
        # Falls back to prior RTH session only when no overnight data exists.
        overnight_today = on_by_date.get(today, [])
        if overnight_today:
            vwap = _compute_vwap(overnight_today)
        else:
            prior_vwap = None
            for d in sorted(rth_by_date.keys(), reverse=True):
                if d < today:
                    prior_vwap = _compute_vwap(rth_by_date[d])
                    if prior_vwap:
                        break
            vwap = prior_vwap

    # ── RTH gap: 9:30 AM open vs prior CME settlement ────────────────────────
    # Both sides must use the same reference so strip and panel agree:
    #   baseline = prev_settle from state['market_bias'] (last - net_change = CME settlement)
    #   open     = first RTH 1-min bar (>= 9:30 ET), NOT the 6 PM overnight open
    #
    # Falls back to state['prev_close'] (prior RTH 4 PM close) when market_bias
    # isn't populated yet (cold start / non-market futures).
    gap = None
    _sym_obj = next((s for s in state['symbols'] if s['ticker'] == symbol), None)
    _sym_obj_id = _sym_obj['id'] if _sym_obj else None
    _mb = state['market_bias'].get(_sym_obj_id, {}) if _sym_obj_id else {}
    # Prefer CME settlement from market_bias; fall back to prior RTH close
    _prev_settle_panel = _mb.get('prev_close') or state['prev_close'].get(_sym_obj_id) or prev_close
    # RTH open: use stored rth_open (set each session); fall back to first RTH bar
    _rth_open_panel = _mb.get('rth_open') or (state['rth_open'].get(_sym_obj_id) if _sym_obj_id else None)
    if not _rth_open_panel:
        # Last resort: first bar in today's RTH window
        today_rth_sorted = sorted(rth_by_date.get(today, []), key=lambda c: c['datetime'])
        if today_rth_sorted:
            _rth_open_panel = today_rth_sorted[0]['open']
    if _rth_open_panel and _prev_settle_panel:
        gap = round(_rth_open_panel - _prev_settle_panel, 2)

    # Initial Balance from 9:30–10:00 ET bars (first 30 min of RTH).
    # Scope to today only; fall back to most recent prior day when pre-market.
    # (raw_1min spans 5 days so naively including all dates would mix multiple IB windows.)
    ib_s_levels = 9 * 60 + 30
    ib_e_levels = 10 * 60
    today_ib_bars: list[dict] = []
    prior_ib_by_date: dict = {}
    for c in raw_1min:
        dt_c    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        t_min_c = dt_c.hour * 60 + dt_c.minute
        c_date  = dt_c.date()
        if ib_s_levels <= t_min_c < ib_e_levels:
            if c_date == today:
                today_ib_bars.append(c)
            elif c_date < today:
                prior_ib_by_date.setdefault(c_date, []).append(c)

    if today_ib_bars:
        ib_high    = max(c['high'] for c in today_ib_bars)
        ib_low     = min(c['low']  for c in today_ib_bars)
        ib_source  = 'today'
    elif prior_ib_by_date:
        _ib_prior_date = max(prior_ib_by_date.keys())   # most recent prior day with IB bars
        _ib_prior_bars = prior_ib_by_date[_ib_prior_date]
        ib_high   = max(c['high'] for c in _ib_prior_bars)
        ib_low    = min(c['low']  for c in _ib_prior_bars)
        ib_source = 'prior'
    else:
        ib_high = ib_low = None
        ib_source = None

    # ib_complete = today's IB window has fully closed (after 10:30 on a weekday)
    ib_complete = (
        ib_source == 'today'
        and now_et.weekday() < 5
        and now_et.hour * 60 + now_et.minute >= ib_e_levels
    )

    _result = {
        'symbol':      symbol,
        'tick':        tick,
        'computed_at': now_et.strftime('%Y-%m-%d %H:%M ET'),
        'gap':         gap,
        'ib_high':     ib_high,
        'ib_low':      ib_low,
        'ib_complete': ib_complete,
        'ib_source':   ib_source,    # 'today' | 'prior' | null
        'levels': {
            # ── Prior RTH (volume profile) ───────────────────────────────────
            'prior_rth_vah':       prior_rth_vah,
            'prior_rth_vpoc':      prior_rth_vpoc,
            'prior_rth_val':       prior_rth_val,
            # ── Prior RTH (Dalton TPO) ───────────────────────────────────────
            'prior_rth_tpo_vah':   prior_rth_tpo_vah,
            'prior_rth_tpo_vpoc':  prior_rth_tpo_vpoc,
            'prior_rth_tpo_val':   prior_rth_tpo_val,
            # ── Overnight (volume profile) ───────────────────────────────────
            'overnight_vah':       overnight_vah,
            'overnight_vpoc':      overnight_vpoc,
            'overnight_val':       overnight_val,
            # ── Overnight (Dalton TPO) ───────────────────────────────────────
            'overnight_tpo_vah':   overnight_tpo_vah,
            'overnight_tpo_vpoc':  overnight_tpo_vpoc,
            'overnight_tpo_val':   overnight_tpo_val,
            # ── Developing RTH (volume profile) ─────────────────────────────
            'developing_vah':      developing_vah,
            'developing_vpoc':     developing_vpoc,
            'developing_val':      developing_val,
            # ── Developing RTH (Dalton TPO) ─────────────────────────────────
            'developing_tpo_vah':  developing_tpo_vah,
            'developing_tpo_vpoc': developing_tpo_vpoc,
            'developing_tpo_val':  developing_tpo_val,
            # ── Other ────────────────────────────────────────────────────────
            'mcvpoc_3day':         mcvpoc_3day,
            'daily_pivot':         daily_pivot,
            'prev_high':           prev_high,
            'prev_low':            prev_low,
            'prev_close':          prev_close,
            'vwap':                vwap,
        }
    }
    _LEVELS_CACHE[symbol] = {'data': _result, 'ts': now_et}
    return _result


@app.get('/api/ytd')
async def get_ytd(symbols: str = Query(...)):
    """Return YTD % change (Jan 1 first-trading-day open → current last price)
    for a comma-separated list of tickers.  Used by ETF panel for ETF vs SPY comparison."""
    tickers = [s.strip().upper() for s in symbols.split(',') if s.strip()][:6]
    if not tickers:
        return {}

    current_year = datetime.now(ET).year

    # Fetch live quotes for all tickers in one shot
    try:
        quotes = await asyncio.to_thread(get_quotes, tickers)
    except Exception:
        quotes = {}

    # Fetch daily candles concurrently
    async def _ytd_for(ticker: str) -> tuple[str, float | None]:
        try:
            candles = await asyncio.to_thread(get_daily_candles, ticker, 366)
            if not candles:
                return ticker, None
            year_candles = [
                c for c in candles
                if datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
                          .astimezone(ET).year == current_year
            ]
            if not year_candles:
                return ticker, None
            jan_open = year_candles[0]['open']
            q        = quotes.get(ticker, {})
            current  = (q.get('last') or 0) or year_candles[-1]['close']
            if jan_open and current:
                return ticker, round((current - jan_open) / jan_open * 100, 2)
        except Exception as e:
            log.warning('YTD %s: %s', ticker, e)
        return ticker, None

    pairs = await asyncio.gather(*[_ytd_for(t) for t in tickers])
    return {t: pct for t, pct in pairs}


@app.get('/api/symbols')
def get_symbols_list():
    return {'symbols': [
        {
            'id'        : s['id'],
            'ticker'    : s['ticker'],
            'asset_type': s.get('asset_type', 'equity'),
            'last_price': state['last_price'].get(s['id']),
            'prev_close': state['prev_close'].get(s['id']),
            'net_change': state['net_change'].get(s['id']),
        }
        for s in state['symbols']
    ]}


@app.get('/api/quotes')
async def get_holding_quotes(symbols: str = Query(...)):
    """Fetch live quotes for a comma-separated list of tickers — used by ETF panel holdings."""
    tickers = [s.strip().upper() for s in symbols.split(',') if s.strip()][:20]  # cap at 20
    if not tickers:
        return {}
    try:
        raw = await asyncio.to_thread(get_quotes, tickers)
    except Exception as e:
        log.warning('get_holding_quotes error: %s', e)
        return {}
    result = {}
    for ticker, q in raw.items():
        last       = q.get('last', 0) or 0
        net_change = q.get('net_change', 0) or 0
        ref        = last - net_change
        change_pct = round(net_change / ref * 100, 2) if ref else 0.0
        # Trim Schwab's verbose descriptions (e.g. "NVIDIA CORPORATION" → "NVIDIA Corp")
        desc = (q.get('description', '') or '').strip()
        result[ticker] = {
            'last'     : round(last, 2)       if last       else None,
            'change'   : round(net_change, 2) if net_change else None,
            'changePct': change_pct           if net_change else None,
            'name'     : desc                 if desc        else None,
        }
    return result


@app.get('/api/etf-holdings/{ticker}')
def get_etf_holdings_endpoint(ticker: str):
    """Return cached top-10 holdings for an ETF (populated by daily Yahoo Finance refresh)."""
    holdings = get_etf_holdings(ticker.upper())
    return {'ticker': ticker.upper(), 'holdings': holdings}


@app.post('/api/etf-holdings/refresh')
async def force_refresh_holdings():
    """Manually trigger a full ETF holdings refresh from Yahoo Finance."""
    asyncio.create_task(refresh_etf_holdings())
    return {'message': 'ETF holdings refresh started'}


@app.get('/api/instrument-search')
def instrument_search(symbol: str = Query(...), projection: str = Query('symbol-search')):
    """Proxy to Schwab instruments endpoint — for diagnostics."""
    import requests as _r
    from schwab_client import _headers
    resp = _r.get('https://api.schwabapi.com/marketdata/v1/instruments',
                  headers=_headers(),
                  params={'symbol': symbol, 'projection': projection},
                  timeout=10)
    return resp.json()


@app.post('/api/refresh-stats')
async def force_refresh():
    asyncio.create_task(compute_all_stats())
    return {'message': 'Stats recomputation started'}


@app.post('/api/reload-db-stats')
async def reload_db_stats():
    """Hot-reload VBH constants from DB without recomputing from Schwab.

    Reloads vbh_engine._stats_db from the vbh_stats table, then updates
    state['stats_*'] for all futures immediately. Use this after importing
    weekly ThinkScript constants so live signals reflect the new values
    without waiting for the 24h compute_all_stats() cycle.
    """
    vbh_engine.load_stats_from_db()
    all_syms = state['symbols']
    futures_n = 0
    stocks_n  = 0
    for sym in all_syms:
        sid = sym['id']
        api = sym['schwab_symbol']
        # Empty candles → engine checks _stats_db first.
        # Futures: always have ThinkScript rows (lookback_days=-1).
        # Stocks: ThinkScript rows for 124 VBH bundle symbols → signals active;
        #         uncovered stocks → {} (no signals).
        state['stats_agg'][sid]  = vbh_engine.compute_stats([], api)
        state['stats_con'][sid]  = vbh_engine.compute_stats_con([], api)
        state['stats_wide'][sid] = vbh_engine.compute_stats_wide([], api)
        if sym['ticker'].startswith('/'):
            futures_n += 1
        else:
            stocks_n += 1
    return {'reloaded_futures': futures_n, 'reloaded_stocks': stocks_n,
            'db_symbols': len(vbh_engine._stats_db)}


# ── AI Futures Agent ───────────────────────────────────────────────────────────

AGENT_FUTURES = ['/ES', '/NQ', '/YM', '/RTY', '/GC']

# Cache: stores last Claude narrative + timestamp so we only call API every 15 min
_agent_cache: dict = {'data': None, 'ts': 0.0, 'rules_data': None}
_AGENT_NARRATIVE_TTL = 900   # 15 minutes between Claude calls (was 5 min — too frequent)
_AGENT_RULES_TTL     = 60    # rule engine refreshes every 60 s


def _agent_bias(price: float, levels: dict) -> dict:
    """Score directional bias from key level relationships."""
    score = 0
    reasons = []

    def _check(val, name):
        nonlocal score
        if val is None:
            return
        if price > val:
            score += 1
            reasons.append(f'above {name} ({val:.2f})')
        else:
            score -= 1
            reasons.append(f'below {name} ({val:.2f})')

    _check(levels.get('daily_pivot'),    'Daily Pivot')
    _check(levels.get('vwap'),           'VWAP')
    _check(levels.get('prior_rth_vpoc'), 'Prior RTH POC')

    direction = 'BULL' if score > 0 else ('BEAR' if score < 0 else 'NEUTRAL')
    return {'score': score, 'direction': direction, 'reasons': reasons}


def _agent_nearest_levels(price: float, levels: dict, naked_vpocs: list) -> dict:
    """Find 2 nearest levels above and 2 below current price."""
    label_map = {
        'prior_rth_vah'  : 'Prior RTH VAH',
        'prior_rth_vpoc' : 'Prior RTH POC',
        'prior_rth_val'  : 'Prior RTH VAL',
        'overnight_vah'  : 'Overnight VAH',
        'overnight_vpoc' : 'Overnight POC',
        'overnight_val'  : 'Overnight VAL',
        'developing_vah' : 'Developing VAH',
        'developing_vpoc': 'Developing POC',
        'developing_val' : 'Developing VAL',
        'mcvpoc_3day'    : '3-Day MCVPOC',
        'daily_pivot'    : 'Daily Pivot',
        'prev_high'      : 'Prior High',
        'prev_low'       : 'Prior Low',
        'vwap'           : 'VWAP',
    }
    all_levels = []
    for key, val in levels.items():
        # Skip None, booleans (e.g. ib_complete), and anything non-numeric
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        all_levels.append({'name': label_map.get(key, key), 'price': val, 'type': 'key'})
    for nv in naked_vpocs:
        date_short = nv['date'][5:]   # MM-DD
        all_levels.append({'name': f'Naked POC {date_short}', 'price': nv['vpoc'], 'type': 'naked'})

    above = sorted([l for l in all_levels if l['price'] > price], key=lambda x: x['price'])[:2]
    below = sorted([l for l in all_levels if l['price'] < price], key=lambda x: x['price'], reverse=True)[:2]

    return {'above': above, 'below': below}


# Stop buffer per symbol (pts beyond the invalidation level)
_STOP_BUFFER = {'/ES': 4, '/NQ': 15, '/YM': 40, '/RTY': 2.5, '/GC': 4}

def _agent_entry_stop(symbol: str, price: float, direction: str, nearest: dict) -> dict | None:
    """Compute entry price and stop loss based on bias and nearest levels.

    BEAR: entry = nearest resistance above (sell the rally), stop = entry + buffer
    BULL: entry = nearest support below (buy the dip),       stop = entry - buffer
    NEUTRAL: no trade suggested
    """
    if direction == 'NEUTRAL':
        return None

    buf = _STOP_BUFFER.get(symbol, 5)

    if direction == 'BEAR':
        resistance = nearest['above'][0] if nearest['above'] else None
        if resistance:
            entry = resistance['price']
            stop  = round(entry + buf, 4)
            risk  = round(stop - entry, 4)
        else:
            # Already below all levels — enter at market
            entry = price
            support = nearest['below'][0] if nearest['below'] else None
            stop  = round((support['price'] + buf) if support else price + buf * 2, 4)
            risk  = round(abs(stop - entry), 4)
        return {
            'side' : 'SHORT',
            'entry': entry,
            'stop' : stop,
            'risk' : risk,
        }

    else:  # BULL
        support = nearest['below'][0] if nearest['below'] else None
        if support:
            entry = support['price']
            stop  = round(entry - buf, 4)
            risk  = round(entry - stop, 4)
        else:
            # Already above all levels — enter at market
            entry = price
            resistance = nearest['above'][0] if nearest['above'] else None
            stop  = round((resistance['price'] - buf) if resistance else price - buf * 2, 4)
            risk  = round(abs(entry - stop), 4)
        return {
            'side' : 'LONG',
            'entry': entry,
            'stop' : stop,
            'risk' : risk,
        }


def _agent_targets(price: float, direction: str, nearest: dict, naked_vpocs: list) -> dict:
    """Assign T1/T2/T3 based on bias direction and nearest levels."""
    if direction == 'BULL':
        candidates = nearest['above']
        nvpoc_candidates = sorted(
            [n for n in naked_vpocs if n['vpoc'] > price],
            key=lambda x: x['vpoc']
        )
    else:
        candidates = nearest['below']
        nvpoc_candidates = sorted(
            [n for n in naked_vpocs if n['vpoc'] < price],
            key=lambda x: x['vpoc'], reverse=True
        )

    t1 = candidates[0] if len(candidates) > 0 else None
    t2 = candidates[1] if len(candidates) > 1 else None

    # T3 = oldest/furthest naked VPOC
    t3 = None
    if nvpoc_candidates:
        # Prefer the one furthest away (strongest pull)
        t3_nv = nvpoc_candidates[-1]
        t3 = {'name': f"Naked POC {t3_nv['date'][5:]}", 'price': t3_nv['vpoc'], 'type': 'naked'}

    return {
        't1': t1,
        't2': t2,
        't3': t3,
    }


async def _fetch_agent_symbol_data(symbol: str) -> dict | None:
    """Fetch all data needed for one symbol: quote + levels + naked VPOCs.
    Each Schwab call is wrapped individually so a single failure doesn't lose the whole symbol.
    """
    try:
        contract = front_month_code(symbol)

        async def _safe(coro, default):
            try:
                return await coro
            except Exception as exc:
                log.warning('_fetch_agent_symbol_data %s sub-call failed: %s', symbol, exc)
                return default

        raw_1min, daily, quotes_raw, nvpoc_raw = await asyncio.gather(
            _safe(asyncio.to_thread(get_candles, symbol, 5, 1),  []),
            _safe(asyncio.to_thread(get_daily_candles, symbol, 30), []),
            _safe(asyncio.to_thread(get_quotes, [contract]),      {}),
            _safe(asyncio.to_thread(get_candles, symbol, 30, 1), []),
        )

        # Fallback to state cache if live quote missing
        if not quotes_raw or not quotes_raw.get(contract):
            sym_obj = next((s for s in state['symbols'] if s['schwab_symbol'] == contract), None)
            sid     = sym_obj['id'] if sym_obj else None
            if sid and state['last_price'].get(sid):
                lp = state['last_price'][sid]
                nc = state['net_change'].get(sid, 0)
                quotes_raw = {contract: {
                    'last': lp, 'net_change': nc,
                    'open': state['rth_open'].get(sid, lp),
                    'close': state['prev_close'].get(sid, lp),
                }}

        # ── Build levels (reuse same logic as /api/levels) ─────────────────────
        tick    = _tick_for(symbol)
        now_et  = datetime.now(ET)
        today   = now_et.date()

        rth_by_date: dict = {}
        on_by_date:  dict = {}
        for c in raw_1min:
            dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
            d     = dt.date()
            t_min = dt.hour * 60 + dt.minute
            wday  = dt.weekday()
            is_rth      = wday < 5 and (9*60+30) <= t_min <= 16*60
            is_on_wkday = wday < 5 and t_min < (9*60+30)
            is_evening  = wday < 4 and t_min >= 18*60
            is_sunday   = wday == 6
            if is_rth:
                rth_by_date.setdefault(d, []).append(c)
            elif is_on_wkday:
                on_by_date.setdefault(d, []).append(c)
            elif is_evening or is_sunday:
                on_by_date.setdefault(d + timedelta(days=1), []).append(c)

        sorted_rth = sorted(rth_by_date.keys(), reverse=True)

        prior_rth_dates_agent = [d for d in sorted_rth if d < today]
        prior_rth_tpo_vpoc = prior_rth_tpo_vah = prior_rth_tpo_val = None
        if prior_rth_dates_agent:
            _prior_agent_bars  = rth_by_date.get(prior_rth_dates_agent[0], [])
            _prior_agent_va    = _compute_value_area(_prior_agent_bars, tick)
            prior_rth_vpoc     = _prior_agent_va['poc']
            prior_rth_vah      = _prior_agent_va['vah']
            prior_rth_val      = _prior_agent_va['val']
            _prior_agent_tpo   = _compute_tpo_value_area(_prior_agent_bars, tick)
            prior_rth_tpo_vpoc = _prior_agent_tpo['poc']
            prior_rth_tpo_vah  = _prior_agent_tpo['vah']
            prior_rth_tpo_val  = _prior_agent_tpo['val']
        else:
            prior_rth_vpoc = prior_rth_vah = prior_rth_val = None

        developing_vpoc = developing_vah = developing_val = None
        developing_tpo_vpoc = developing_tpo_vah = developing_tpo_val = None
        _dev_bars_agent = rth_by_date.get(today, [])
        if _dev_bars_agent:
            _dev_va_agent       = _compute_value_area(_dev_bars_agent, tick)
            developing_vpoc     = _dev_va_agent['poc']
            developing_vah      = _dev_va_agent['vah']
            developing_val      = _dev_va_agent['val']
            _dev_tpo_agent      = _compute_tpo_value_area(_dev_bars_agent, tick)
            developing_tpo_vpoc = _dev_tpo_agent['poc']
            developing_tpo_vah  = _dev_tpo_agent['vah']
            developing_tpo_val  = _dev_tpo_agent['val']
        overnight_vpoc = overnight_vah = overnight_val = None
        overnight_tpo_vpoc = overnight_tpo_vah = overnight_tpo_val = None
        _on_bars_agent = on_by_date.get(today, [])
        if _on_bars_agent:
            _on_va_agent       = _compute_value_area(_on_bars_agent, tick)
            overnight_vpoc     = _on_va_agent['poc']
            overnight_vah      = _on_va_agent['vah']
            overnight_val      = _on_va_agent['val']
            _on_tpo_agent      = _compute_tpo_value_area(_on_bars_agent, tick)
            overnight_tpo_vpoc = _on_tpo_agent['poc']
            overnight_tpo_vah  = _on_tpo_agent['vah']
            overnight_tpo_val  = _on_tpo_agent['val']

        mc3 = []
        for d in [d for d in sorted_rth if d < today][:3]:
            mc3.extend(rth_by_date[d])
        mcvpoc_3day = _compute_vpoc(mc3, tick) if mc3 else None

        def _bd(c):
            return datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET).date()

        daily_pivot = weekly_pivot = weekly_open = None
        ath = swing_high = swing_low = prev_high = prev_low = prev_close = None

        prior_daily = [c for c in daily if _bd(c) < today]
        if prior_daily:
            prev = prior_daily[-1]
            prev_high = prev['high']; prev_low = prev['low']; prev_close = prev['close']
            daily_pivot = round((prev['high'] + prev['low'] + prev['close']) / 3, 4)
            ath = max(c['high'] for c in daily)
            # Include today's intraday high (holiday / partial-day sessions miss the daily bar)
            today_bars_all = rth_by_date.get(today, []) + on_by_date.get(today, [])
            if today_bars_all:
                ath = max(ath, max(c['high'] for c in today_bars_all))
            monday_this = today - timedelta(days=today.weekday())
            monday_prev = monday_this - timedelta(days=7)
            this_week = [c for c in daily if _bd(c) >= monday_this]
            if this_week:
                weekly_open = this_week[0]['open']
            prev_week = [c for c in daily if monday_prev <= _bd(c) < monday_this]
            if prev_week:
                weekly_pivot = round((max(c['high'] for c in prev_week) + min(c['low'] for c in prev_week) + prev_week[-1]['close']) / 3, 4)

        today_rth = rth_by_date.get(today, [])
        prior_rth = next((rth_by_date[d] for d in sorted_rth if d < today), [])
        vwap = _compute_vwap(today_rth) or _compute_vwap(prior_rth)

        # IB from state (populated by refresh_signals loop)
        sym_obj_agent = next((s for s in state['symbols'] if s['ticker'] == symbol), None)
        sid_agent     = sym_obj_agent['id'] if sym_obj_agent else None
        ib_data       = state['ib'].get(sid_agent, {}) if sid_agent else {}

        # Gap: RTH open vs prior CME settlement — reuse the value already computed
        # in refresh_signals() (state['market_bias'][sid]['gap']) which uses:
        #   prev_settle = last - net_change  (exact CME settlement, most accurate)
        #   rth_open    = true 9:30 ET open from live Schwab quote
        # Fallback: find the first RTH bar (≥09:30) and diff against prev_close.
        _mb_agent  = state['market_bias'].get(sid_agent, {}) if sid_agent else {}
        gap_agent  = _mb_agent.get('gap')
        if gap_agent is None:
            # Fallback when market_bias not yet populated (e.g. pre-market)
            rth_today = rth_by_date.get(today, [])
            if rth_today:
                rth_open_bar = min(rth_today, key=lambda c: c['datetime'])
                _settle = _mb_agent.get('prev_close') or prev_close
                if _settle:
                    gap_agent = round(rth_open_bar['open'] - _settle, 2)

        levels = {
            'prior_rth_vah': prior_rth_vah, 'prior_rth_vpoc': prior_rth_vpoc, 'prior_rth_val': prior_rth_val,
            'prior_rth_tpo_vah': prior_rth_tpo_vah, 'prior_rth_tpo_vpoc': prior_rth_tpo_vpoc, 'prior_rth_tpo_val': prior_rth_tpo_val,
            'overnight_vah': overnight_vah, 'overnight_vpoc': overnight_vpoc, 'overnight_val': overnight_val,
            'overnight_tpo_vah': overnight_tpo_vah, 'overnight_tpo_vpoc': overnight_tpo_vpoc, 'overnight_tpo_val': overnight_tpo_val,
            'developing_vah': developing_vah, 'developing_vpoc': developing_vpoc, 'developing_val': developing_val,
            'developing_tpo_vah': developing_tpo_vah, 'developing_tpo_vpoc': developing_tpo_vpoc, 'developing_tpo_val': developing_tpo_val,
            'mcvpoc_3day': mcvpoc_3day, 'daily_pivot': daily_pivot,
            'weekly_pivot': weekly_pivot, 'weekly_open': weekly_open,
            'ath_intraday': ath, 'prev_high': prev_high, 'prev_low': prev_low,
            'prev_close': prev_close, 'vwap': vwap,
            'ib_high'    : ib_data.get('high'),
            'ib_low'     : ib_data.get('low'),
            'ib_complete': ib_data.get('complete', False),
            'gap'        : gap_agent,
        }

        # ── Naked VPOCs ─────────────────────────────────────────────────────────
        rth_nvpoc: dict = {}
        for c in nvpoc_raw:
            dt   = datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET)
            d    = dt.date(); t_min = dt.hour * 60 + dt.minute; wday = dt.weekday()
            if wday < 5 and (9*60+30) <= t_min < 16*60:
                rth_nvpoc.setdefault(d, []).append(c)
        sorted_nvpoc_dates = sorted(rth_nvpoc.keys())
        session_vpoc_list = [{'date': d, 'vpoc': _compute_vpoc(rth_nvpoc[d], tick)}
                             for d in sorted_nvpoc_dates if _compute_vpoc(rth_nvpoc[d], tick)]
        naked_vpocs = []
        for i, sv in enumerate(session_vpoc_list):
            touched = any(
                b['low'] <= sv['vpoc'] <= b['high']
                for j in range(i+1, len(session_vpoc_list))
                for b in rth_nvpoc.get(session_vpoc_list[j]['date'], [])
            )
            if not touched:
                naked_vpocs.append({'date': sv['date'].isoformat(), 'vpoc': sv['vpoc']})

        # ── Live quote ──────────────────────────────────────────────────────────
        q          = quotes_raw.get(contract, {})
        price      = q.get('last') or prev_close or 0
        net_change = q.get('net_change', 0) or 0
        ref        = price - net_change
        change_pct = round(net_change / ref * 100, 2) if ref else 0.0

        # ── Rule engine ─────────────────────────────────────────────────────────
        bias       = _agent_bias(price, levels)
        nearest    = _agent_nearest_levels(price, levels, naked_vpocs)
        targets    = _agent_targets(price, bias['direction'], nearest, naked_vpocs)
        entry_stop = _agent_entry_stop(symbol, price, bias['direction'], nearest)

        return {
            'symbol'    : symbol,
            'price'     : price,
            'change'    : round(net_change, 2),
            'change_pct': change_pct,
            'bias'      : bias,
            'nearest'   : nearest,
            'targets'   : targets,
            'entry_stop': entry_stop,
            'naked_vpocs': naked_vpocs,
            'levels'    : levels,
            'tick'      : tick,
        }
    except Exception as e:
        log.warning('_fetch_agent_symbol_data(%s): %s', symbol, e)
        return None


def _sector_pcts() -> dict[str, float]:
    """Intraday % change for all sector ETFs from RTH open."""
    ticker_map = {s['ticker']: s for s in state['symbols']}
    result: dict[str, float] = {}
    for etf in STRIP_ETFS:
        tick = etf['ticker']
        sym  = ticker_map.get(tick)
        if not sym:
            continue
        sid      = sym['id']
        rth_open = state['rth_open'].get(sid, 0)
        last     = state['last_price'].get(sid, 0) or state['prev_close'].get(sid, 0)
        result[tick] = round((last - rth_open) / rth_open * 100, 2) if (rth_open and last) else 0.0
    return result


async def _internals_snapshot() -> dict:
    """Fetch $TICK, $TRIN, $ADVN/$DECN, $VIX/$VXN via existing schwab_client."""
    try:
        raw = await asyncio.to_thread(
            get_quotes, ['$TICK', '$TRIN', '$ADVN', '$DECN', '$VIX', '$VXN']
        )
        def _v(sym): return raw.get(sym, {}).get('last', 0) or 0
        tick = _v('$TICK'); trin = _v('$TRIN')
        advn = int(_v('$ADVN')); decn = int(_v('$DECN'))
        return {
            'tick' : tick,
            'trin' : trin,
            'advn' : advn,
            'decn' : decn,
            'adspd': advn - decn,
            'vix'  : _v('$VIX'),
            'vxn'  : _v('$VXN'),
        }
    except Exception as exc:
        log.warning('_internals_snapshot failed: %s', exc)
        return {}


def _build_claude_prompt(symbols_data: list[dict],
                         signals: list[dict] | None     = None,
                         internals: dict | None          = None,
                         sector_pcts: dict[str, float]  | None = None) -> str:
    """Format all futures into a structured Market Profile prompt for Claude."""
    now_et = datetime.now(ET)
    t_min  = now_et.hour * 60 + now_et.minute
    wday   = now_et.weekday()
    if wday < 5 and (9*60+30) <= t_min < 16*60:
        session_label = 'RTH LIVE'
    elif wday < 5 and 4*60 <= t_min < 9*60+30:
        session_label = 'PRE-MARKET'
    else:
        session_label = 'OVERNIGHT/AFTER-HOURS'

    ib_complete = wday < 5 and t_min >= 10*60   # after 10:00 ET (30-min IB)

    lines = [
        f'Market session: {session_label} — {now_et.strftime("%Y-%m-%d %H:%M ET")}',
        '',
        'MARKET PROFILE DATA:',
    ]

    for d in symbols_data:
        if not d:
            continue
        b   = d['bias']
        t   = d['targets']
        nr  = d['nearest']
        lv  = d.get('levels', {})
        es  = d.get('entry_stop')

        # Value Area context
        vah  = lv.get('session_vah')
        val  = lv.get('session_val')
        vpoc = lv.get('session_vpoc')
        price = d['price']

        if vah and val and price:
            if price > vah:
                va_pos = f'ABOVE Value Area (VAH {vah}) — market seeking acceptance or will reject back'
            elif price < val:
                va_pos = f'BELOW Value Area (VAL {val}) — market seeking acceptance or will reject back up'
            else:
                va_pos = f'INSIDE Value Area ({val}–{vah}) — balanced, two-sided trade expected'
        else:
            va_pos = 'Value Area: N/A'

        # Gap context
        gap = lv.get('gap')
        if gap is not None and abs(gap) >= 0.25:
            gap_str = f'Gap {"UP" if gap > 0 else "DOWN"} {abs(gap):.2f} pts from prior RTH close'
            # Gap fill status
            prev_rth = lv.get('prev_close')
            if prev_rth:
                gap_target = prev_rth
                if gap > 0:
                    gap_filled = price <= gap_target
                else:
                    gap_filled = price >= gap_target
                gap_str += f' — {"FILLED" if gap_filled else f"unfilled, target {gap_target:.2f}"}'
        else:
            gap_str = 'No significant gap'

        # IB context
        ib_high = lv.get('ib_high')
        ib_low  = lv.get('ib_low')
        ib_done = lv.get('ib_complete', False)
        if ib_high and ib_low:
            ib_range = round(ib_high - ib_low, 2)
            ib_status = 'complete' if ib_done else 'developing'
            ib_str = f'IB ({ib_status}): {ib_low}–{ib_high} range={ib_range} pts'
            if ib_done and price:
                if price > ib_high:
                    ib_str += ' | Price ABOVE IB → extension day likely'
                elif price < ib_low:
                    ib_str += ' | Price BELOW IB → extension day likely (downside)'
                else:
                    ib_str += ' | Price inside IB → rotational/balanced day'
        else:
            ib_str = 'IB: pre-market (RTH not yet open)' if not ib_done else 'IB: N/A'

        # Open type inference (only meaningful during RTH)
        open_type = ''
        if session_label == 'RTH LIVE' and vah and val and gap is not None:
            if abs(gap) >= 2.0:   # meaningful gap
                if gap > 0 and price > vah:
                    open_type = 'Open type: Gap-Up above VAH → Open-Drive or Open-Rejection-Reverse'
                elif gap > 0 and val <= price <= vah:
                    open_type = 'Open type: Gap-Up into Value Area → likely Open-Auction, gap fill probable'
                elif gap < 0 and price < val:
                    open_type = 'Open type: Gap-Down below VAL → Open-Drive or Open-Rejection-Reverse'
                elif gap < 0 and val <= price <= vah:
                    open_type = 'Open type: Gap-Down into Value Area → likely Open-Auction, gap fill probable'
            else:
                open_type = 'Open type: Inside prior Value Area → Open-Auction, expect rotation'

        above_str = ', '.join(f"{l['name']} {l['price']}" for l in nr['above']) or 'none'
        below_str = ', '.join(f"{l['name']} {l['price']}" for l in nr['below']) or 'none'
        t1 = f"{t['t1']['name']} {t['t1']['price']}" if t.get('t1') else '—'
        t2 = f"{t['t2']['name']} {t['t2']['price']}" if t.get('t2') else '—'
        t3 = f"{t['t3']['name']} {t['t3']['price']}" if t.get('t3') else '—'
        nvpoc_str = ', '.join(f"{n['vpoc']} ({n['date'][5:]})" for n in d.get('naked_vpocs', [])[-3:]) or 'none'

        es_str = (f"  Entry: {es['side']} @ {es['entry']}  |  Stop: {es['stop']}  |  Risk: {es['risk']} pts"
                  if es else "  Entry: No trade — NEUTRAL bias")

        # VBH signals for this instrument
        sym_base = d['symbol'].split(':')[0]   # '/NQ' from '/NQ:XCME'
        vbh_lines = []
        if signals:
            sym_sigs = [s for s in signals if s.get('symbol', '').split(':')[0] == sym_base]
            for sig in sym_sigs:
                mo  = sig.get('mo_state', '—')
                sqc = sig.get('sq_confirm', '—')
                vbh_lines.append(
                    f"  VBH {sig['model']} {sig['side']} [{sig['signal_state']}] "
                    f"entry:{sig['entry']}  L1:{sig['l1']}  stop:{sig['stop']}  "
                    f"momentum:{mo}  squeeze:{sqc}"
                )

        # Sector alignment for this instrument
        sec_parts = []
        if sector_pcts:
            for ticker, name in SECTORS_FOR.get(sym_base, []):
                pct = sector_pcts.get(ticker, 0.0)
                arrow = '▲' if pct > 0 else ('▼' if pct < 0 else '—')
                sec_parts.append(f"{ticker} {arrow}{abs(pct):.2f}%")
        sec_str = '  Sectors: ' + '  '.join(sec_parts) if sec_parts else ''

        lines += [
            '',
            f"{d['symbol']}  |  Price: {price}  Change: {d['change']:+.2f} ({d['change_pct']:+.2f}%)",
            f"  Bias: {b['direction']} (score {b['score']}/4) — {'; '.join(b['reasons'])}",
            f"  {gap_str}",
            f"  Value Area: POC {vpoc}  VAH {vah}  VAL {val}",
            f"  {va_pos}",
            f"  {ib_str}",
        ]
        if open_type:
            lines.append(f"  {open_type}")
        lines += [
            f"  Levels above: {above_str}",
            f"  Levels below: {below_str}",
            f"  Targets → T1: {t1}  |  T2: {t2}  |  T3: {t3}",
            f"  Naked POCs (unfilled magnets): {nvpoc_str}",
            es_str,
        ]
        if vbh_lines:
            lines.append('  VBH Signals:')
            lines.extend(vbh_lines)
        if sec_str:
            lines.append(sec_str)

    # Market internals block (appended once, after all symbols)
    if internals and any(internals.values()):
        tick  = internals.get('tick', 0)
        trin  = internals.get('trin', 0)
        adspd = internals.get('adspd', 0)
        advn  = internals.get('advn', 0)
        decn  = internals.get('decn', 0)
        vix   = internals.get('vix', 0)
        vxn   = internals.get('vxn', 0)

        tick_bias = ('BULL' if tick > 600 else 'BEAR' if tick < -600
                     else 'mild-bull' if tick > 200 else 'mild-bear' if tick < -200 else 'neutral')
        trin_bias = ('BULL' if trin < 0.8 else 'BEAR' if trin > 1.5 else 'neutral')
        ad_ratio  = f"{advn/decn:.2f}:1" if decn > 0 else '—'

        lines += [
            '',
            'MARKET INTERNALS:',
            f"  $TICK: {tick:+.0f} ({tick_bias})   $TRIN: {trin:.2f} ({trin_bias})",
            f"  A/D:  {ad_ratio} NYSE  ({advn:,}↑ / {decn:,}↓)  ADSPD: {adspd:+,}",
            f"  $VIX: {vix:.2f}   $VXN: {vxn:.2f}",
        ]

    return '\n'.join(lines)


# System prompt — concise. Market Profile knowledge is encoded in _build_claude_prompt data.
# Prompt caching not used for narrative: calls are 15 min apart, Anthropic cache TTL is 5 min
# → cache always cold → write fee (125%) on every call with no read savings.
# Caching will be added to the per-user chat feature where calls cluster within 5 min.
_MP_SYSTEM_PROMPT = (
    'You are a concise futures trading assistant combining Market Profile theory with VBH '
    '(Volume-Based Harmonic) mean-reversion signals and market internals. '
    'For each symbol analyze: open type, Value Area position, VBH signal state (ENTRY = at the zone, '
    'NEAR = approaching), momentum (POS_DN = decelerating = prime entry, NEG_UP = exhausted), '
    'squeeze confirmation, sector alignment, and internals ($TICK >600 = skip shorts, <-600 = skip longs). '
    'Keep it to 2-3 sentences per symbol: key level context, VBH signal quality, one clear action. '
    'Flag conflicts (e.g. VBH SHORT but $TICK >800 = wait). No tables.'
)


# Cache for agent narrative
_narrative_cache: dict = {'text': None, 'ts': 0.0, 'symbols_hash': ''}


@app.get('/api/ai/futures')
async def ai_futures_brief(bust: str | None = None):
    """Rule-based analysis + Claude narrative for the 5 tracked futures.
    Rules engine runs fresh every call. Claude narrative cached for 15 min.
    Pass ?bust=<anything> to bypass the narrative cache (on-demand refresh).
    """
    import os, hashlib

    # Fetch all 5 symbols + internals concurrently
    fetches = [_fetch_agent_symbol_data(s) for s in AGENT_FUTURES]
    *sym_results, internals = await asyncio.gather(*fetches, _internals_snapshot())
    valid = [r for r in sym_results if r is not None]

    if not valid:
        log.warning('ai_futures_brief: all symbol fetches failed — returning empty response')
        return {
            'generated_at': datetime.now(ET).strftime('%Y-%m-%d %H:%M ET'),
            'narrative'   : None,
            'symbols'     : [],
        }

    # Build a hash of current prices to detect significant moves
    # Hash only bias direction — narrative only needs regeneration when market
    # structure changes (BULL→BEAR), not on every price tick.
    # TTL is the primary guard (15 min); bias flip forces an early refresh.
    bias_hash = hashlib.md5(
        '|'.join(f"{r['symbol']}:{r['bias']['direction']}" for r in valid).encode()
    ).hexdigest()[:8]

    now_ts  = datetime.now(ET).timestamp()
    cache   = _narrative_cache
    use_cache = (
        bust is None and           # ?bust param forces a fresh Claude call
        cache['text'] and
        (now_ts - cache['ts']) < _AGENT_NARRATIVE_TTL and
        cache['symbols_hash'] == bias_hash
    )

    narrative = cache['text'] if use_cache else None

    if not use_cache:
        try:
            import anthropic
            api_key = os.environ.get('ANTHROPIC_API_KEY', '')
            if api_key:
                client = anthropic.Anthropic(api_key=api_key)
                prompt = _build_claude_prompt(
                    valid,
                    signals     = state.get('signals', []),
                    internals   = internals,
                    sector_pcts = _sector_pcts(),
                )
                message = client.messages.create(
                    model='claude-haiku-4-5',
                    max_tokens=900,
                    system=_MP_SYSTEM_PROMPT,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                narrative = message.content[0].text
                _narrative_cache['text']         = narrative
                _narrative_cache['ts']           = now_ts
                _narrative_cache['symbols_hash'] = bias_hash
                log.info(
                    'Claude narrative — in: %d  out: %d  (next call in ~15 min or on bias flip)',
                    message.usage.input_tokens,
                    message.usage.output_tokens,
                )
        except Exception as e:
            log.warning('Claude API error: %s', e)
            narrative = None

    now_et = datetime.now(ET)
    return {
        'generated_at'   : now_et.strftime('%Y-%m-%d %H:%M ET'),
        'generated_at_ts': int(now_et.timestamp() * 1000),   # UTC ms — timezone-safe for browser
        'narrative'      : narrative,
        'symbols'        : valid,
    }


# ── Fibonacci S/R Engine ──────────────────────────────────────────────────────

_FIB_RATIOS = [
    (0.000, '0%'),
    (0.236, '23.6%'),
    (0.382, '38.2%'),
    (0.500, '50%'),
    (0.618, '61.8%'),
    (0.786, '78.6%'),
    (1.000, '100%'),
]

def _compute_fib_sr(bars: list[dict], price_key: str = 'bar_time') -> dict:
    """
    Fibonacci retracement S/R — 90-day absolute high/low range.

    Uses the full 90-day absolute high and low as the Fib anchor points,
    matching ThinkorSwim when configured for a 90-day daily chart.

    Direction (matches TOS upward/downward):
      high occurred last  → downtrend → levels = low  + range × ratio
      low  occurred last  → uptrend   → levels = high − range × ratio
    """
    if len(bars) < 5:
        return {'resistance': [], 'support': [], 'fib_high': None, 'fib_low': None,
                'current_price': 0, 'bars': 0}

    def _ts(b):
        k = b.get('bar_time') or b.get('date') or ''
        try:
            return datetime.fromisoformat(str(k)).timestamp()
        except Exception:
            return float(b.get('t', 0))

    sorted_bars   = sorted(bars, key=_ts)
    current_price = float(sorted_bars[-1]['close'])

    # 90-day absolute extremes — no fractal filtering
    swing_high = max(float(b['high']) for b in sorted_bars)
    swing_low  = min(float(b['low'])  for b in sorted_bars)
    high_idx   = max(i for i, b in enumerate(sorted_bars) if float(b['high']) == swing_high)
    low_idx    = max(i for i, b in enumerate(sorted_bars) if float(b['low'])  == swing_low)
    diff = swing_high - swing_low

    if diff < 0.01:
        return {'resistance': [], 'support': [], 'fib_high': swing_high, 'fib_low': swing_low,
                'current_price': current_price, 'bars': len(sorted_bars)}

    # Match TOS direction logic:
    #   high came last (uptrend)   → draw Fib from HIGH downward: 0% = high, 100% = low
    #   low  came last (downtrend) → draw Fib from LOW  upward:   0% = low,  100% = high
    high_came_last = high_idx > low_idx

    levels = []
    for ratio, label in _FIB_RATIOS:
        if high_came_last:
            price = round(swing_high - diff * ratio, 4)   # 0%=high → 100%=low
        else:
            price = round(swing_low  + diff * ratio, 4)   # 0%=low  → 100%=high

        dist_pct = round((price - current_price) / current_price * 100, 2) if current_price else 0
        levels.append({
            'price'    : price,
            'zone_type': label,
            'touches'  : 1,
            'dist_pct' : dist_pct,
        })

    resistance = sorted([l for l in levels if l['price'] > current_price], key=lambda x: x['price'])
    support    = sorted([l for l in levels if l['price'] < current_price], key=lambda x: x['price'], reverse=True)

    return {
        'resistance'   : resistance,
        'support'      : support,
        'current_price': current_price,
        'fib_high'     : round(swing_high, 4),
        'fib_low'      : round(swing_low,  4),
        'direction'    : 'high_last' if high_came_last else 'low_last',
        'bars'         : len(sorted_bars),
    }


# ── Swing High/Low S/R Engine (kept for chat context) ─────────────────────────

def _compute_sr_levels(bars: list[dict], n: int = 5, cluster_pct: float = 0.004) -> dict:
    """
    Detect intraday support & resistance from 1-min bars.

    Steps:
    1. Find swing highs (resistance pivots) and swing lows (support pivots)
       using n-bar lookback/lookahead.
    2. Cluster nearby pivots within cluster_pct of each other.
    3. Score by touch count and volume.
    4. Return top levels sorted by strength.
    """
    if len(bars) < n * 2 + 1:
        return {'resistance': [], 'support': []}

    # Convert bar_time strings to epoch ms for ordering
    def _bar_ms(b):
        try:
            return int(datetime.fromisoformat(b['bar_time']).timestamp() * 1000)
        except Exception:
            return b.get('t', 0)

    sorted_bars = sorted(bars, key=_bar_ms)
    nb = len(sorted_bars)

    raw_res: list[dict] = []   # swing highs → resistance
    raw_sup: list[dict] = []   # swing lows  → support

    for i in range(n, nb - n):
        h = sorted_bars[i]['high']
        l = sorted_bars[i]['low']
        vol = sorted_bars[i].get('volume', 0) or 0
        bt  = sorted_bars[i].get('bar_time', '')

        # Swing high: highest of the window
        if all(h >= sorted_bars[i - j]['high'] for j in range(1, n + 1)) and \
           all(h >= sorted_bars[i + j]['high'] for j in range(1, n + 1)):
            raw_res.append({'price': h, 'time': bt, 'volume': vol})

        # Swing low: lowest of the window
        if all(l <= sorted_bars[i - j]['low'] for j in range(1, n + 1)) and \
           all(l <= sorted_bars[i + j]['low'] for j in range(1, n + 1)):
            raw_sup.append({'price': l, 'time': bt, 'volume': vol})

    def _cluster(points):
        if not points:
            return []
        pts = sorted(points, key=lambda x: x['price'])
        clusters: list[list] = [[pts[0]]]
        for p in pts[1:]:
            base = clusters[-1][0]['price']
            if base > 0 and (p['price'] - base) / base <= cluster_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])

        result = []
        for cl in clusters:
            avg_price  = round(sum(x['price']  for x in cl) / len(cl), 4)
            total_vol  = sum(x['volume'] for x in cl)
            touches    = len(cl)
            last_touch = max(x['time'] for x in cl)
            result.append({
                'price'     : avg_price,
                'touches'   : touches,
                'volume'    : total_vol,
                'last_touch': last_touch,
                'strength'  : round(touches + min(total_vol / 100_000, 5), 2),
            })
        return sorted(result, key=lambda x: x['strength'], reverse=True)

    resistance = _cluster(raw_res)[:8]
    support    = _cluster(raw_sup)[:8]

    # Mark strong zones (3+ touches or high volume)
    current_price = sorted_bars[-1]['close'] if sorted_bars else 0
    for r in resistance:
        r['zone_type'] = 'supply'   if r['touches'] >= 3 else 'resistance'
        r['dist_pct']  = round((r['price'] - current_price) / current_price * 100, 2) if current_price else 0
    for s in support:
        s['zone_type'] = 'demand'   if s['touches'] >= 3 else 'support'
        s['dist_pct']  = round((s['price'] - current_price) / current_price * 100, 2) if current_price else 0

    return {
        'resistance'   : resistance,
        'support'      : support,
        'current_price': round(current_price, 4),
        'bars_analyzed': nb,
    }


@app.get('/api/sr/{ticker}')
async def get_sr_levels(ticker: str, days: int = Query(3)):
    """Return intraday support & resistance zones for a stock from stored 1-min data."""
    ticker = ticker.upper()
    rows   = await asyncio.to_thread(get_ticker_candles, ticker, days)
    if not rows:
        # Fallback to live Schwab
        try:
            raw  = await asyncio.to_thread(get_candles, ticker, days, 1)
            rows = [{'bar_time': datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).isoformat(),
                     'high': c['high'], 'low': c['low'], 'close': c['close'],
                     'volume': c.get('volume', 0)} for c in raw]
        except Exception:
            return {'ticker': ticker, 'error': 'No data available'}

    result = _compute_sr_levels(rows)
    result['ticker'] = ticker
    result['days']   = days
    return result


# ── Fib level cache — daily candles don't change intraday ─────────────────────
# {ticker: {'data': {...}, 'ts': float}}  TTL = 1 hour
_fib_cache: dict[str, dict] = {}
_FIB_CACHE_TTL = 3600   # 1 hour

def _fib_cache_get(ticker: str) -> dict | None:
    entry = _fib_cache.get(ticker)
    if entry and (time.time() - entry['ts']) < _FIB_CACHE_TTL:
        return entry['data']
    return None

def _fib_cache_set(ticker: str, data: dict) -> None:
    _fib_cache[ticker] = {'data': data, 'ts': time.time()}


@app.get('/api/sr')
async def get_sr_batch(tickers: str = Query(...)):
    """Batch Fib S/R. ONE DB query for all tickers, then Schwab only for gaps."""
    ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()][:30]
    if not ticker_list:
        return {}

    result: dict[str, dict] = {}

    # 1. In-memory cache — check all tickers first
    missing = []
    for t in ticker_list:
        cached = _fib_cache_get(t)
        if cached:
            result[t] = cached
        else:
            missing.append(t)

    if not missing:
        return result   # all cached — instant response

    # 2. ONE batch DB query for all missing tickers
    db_batch = await asyncio.to_thread(get_daily_candles_batch, missing, 90)

    # Require at least 60 trading days — a smaller window gives a compressed
    # Fib range that may sit entirely below/above the live price, producing
    # all-support or all-resistance and hiding the other side.  Fall through
    # to Schwab if the DB doesn't have enough history yet.
    MIN_BARS = 60

    still_missing = []
    for ticker, db_rows in db_batch.items():
        if len(db_rows) >= MIN_BARS:
            bars = [{'bar_time': r['bar_date'] + 'T00:00:00+00:00',
                     'high': float(r['high']), 'low': float(r['low']),
                     'close': float(r['close']), 'open': float(r['open'])}
                    for r in db_rows]
            sr = _compute_fib_sr(bars)
            entry = {
                'resistance'  : sr['resistance'],
                'support'     : sr['support'],
                'candle_price': sr['current_price'],
                'fib_high'    : sr['fib_high'],
                'fib_low'     : sr['fib_low'],
                'direction'   : sr.get('direction', 'unknown'),
                'bars'        : sr['bars'],
            }
            _fib_cache_set(ticker, entry)
            result[ticker] = entry
        else:
            # DB has too few bars (or none) — fetch from Schwab for proper 90-day range
            still_missing.append(ticker)
            if db_rows:
                log.debug('SR %s: only %d DB bars < %d minimum, falling back to Schwab', ticker, len(db_rows), MIN_BARS)

    # 3. Schwab fallback for tickers with insufficient DB history
    if still_missing:
        sem = asyncio.Semaphore(8)

        async def fetch_from_schwab(ticker: str) -> tuple[str, dict]:
            async with sem:
                try:
                    raw  = await asyncio.to_thread(get_daily_candles, ticker, 90)
                    rows = _schwab_daily_to_rows(ticker, raw)
                    if rows:
                        await asyncio.to_thread(upsert_daily_candles, rows)
                    bars = [{'bar_time': r['bar_date'] + 'T00:00:00+00:00',
                             'high': r['high'], 'low': r['low'],
                             'close': r['close'], 'open': r['open']}
                            for r in rows]
                    if not bars:
                        return ticker, {}
                    sr    = _compute_fib_sr(bars)
                    entry = {
                        'resistance'  : sr['resistance'],
                        'support'     : sr['support'],
                        'candle_price': sr['current_price'],
                        'fib_high'    : sr['fib_high'],
                        'fib_low'     : sr['fib_low'],
                        'direction'   : sr.get('direction', 'unknown'),
                        'bars'        : sr['bars'],
                    }
                    _fib_cache_set(ticker, entry)
                    return ticker, entry
                except Exception as e:
                    log.debug('SR schwab fallback %s: %s', ticker, e)
                    return ticker, {}

        schwab_results = await asyncio.gather(*[fetch_from_schwab(t) for t in still_missing])
        for ticker, entry in schwab_results:
            if entry:
                result[ticker] = entry

    return result


# ── Global Markets (Asian indices + FX risk-on/off) ────────────────────────────

def _fetch_asia_yfinance() -> list[dict]:
    """Fetch Asian index daily close % change via yfinance (per-symbol to avoid batch failure).
    yfinance 0.2.x handles Yahoo Finance crumb/cookie auth automatically.
    Fetching per-symbol means one bad index doesn't block the rest.
    Returns [] only if ALL symbols fail — partial results are normal."""
    import yfinance as yf
    result = []
    for item in ASIAN_INDICES:
        try:
            ticker = yf.Ticker(item['symbol'])
            hist   = ticker.history(period='5d', interval='1d', auto_adjust=True)
            if hist is None or len(hist) < 2:
                log.debug('Asia yfinance %s: < 2 rows', item['symbol'])
                continue
            prev = float(hist['Close'].iloc[-2])
            last = float(hist['Close'].iloc[-1])
            result.append({
                'name'      : item['name'],
                'region'    : item['region'],
                'close'     : round(last, 2),
                'change_pct': round((last - prev) / prev * 100, 2),
            })
        except Exception as e:
            log.warning('Asia yfinance %s error: %s', item['symbol'], e)
        time.sleep(0.4)   # gentle pacing between symbols
    log.warning('Asia yfinance fetch: %d/%d indices', len(result), len(ASIAN_INDICES))
    return result


def _fetch_fx_schwab() -> list[dict]:
    """Fetch FX rates from Schwab quotes API. Returns [] on failure."""
    try:
        syms   = [p['schwab_symbol'] for p in FX_PAIRS]
        quotes = get_quotes(syms)
        result = []
        for item in FX_PAIRS:
            q = quotes.get(item['schwab_symbol'], {})
            last  = q.get('last', 0)
            close = q.get('close', 0)   # previous session close from Schwab
            if last and close:
                chg_pct = (last - close) / close * 100
                result.append({
                    'name'      : item['name'],
                    'risk'      : item['risk'],
                    'rate'      : round(last, 4),
                    'change_pct': round(chg_pct, 4),
                })
        log.info('FX Schwab: %d pairs', len(result))
        return result
    except Exception as e:
        log.warning('FX Schwab fetch error: %s', e)
        return []


async def _refresh_global_markets() -> dict:
    """
    Build the global-markets payload:
      • FX   — live from Schwab quotes (refreshed every 15 min)
      • Asia — Yahoo Finance v8 direct API; cached in DB; refreshed every 30 min
                during Asian session hours (6 PM – 8 AM ET), daily otherwise.
    """
    from db import cache_get, cache_set

    # ── FX from Schwab ─────────────────────────────────────────────────────────
    fx = await asyncio.to_thread(_fetch_fx_schwab)

    # ── Asian indices: refresh cadence depends on session hours ────────────────
    now_et    = datetime.now(ET)
    today_str = now_et.date().isoformat()
    et_hour   = now_et.hour

    # Asian session roughly 6 PM – 8 AM ET; refresh every 30 min during that window
    # and daily otherwise (markets are closed, data is stale anyway).
    is_asian_session = et_hour >= 18 or et_hour < 8
    ASIA_TTL_SECS    = 1800 if is_asian_session else 86400

    asia: list[dict] = []
    cached = await asyncio.to_thread(cache_get, 'global_markets_asia')

    # Use cache if fresh enough
    if cached and cached.get('asia'):
        fetched_at = cached.get('fetched_at', 0)
        age        = time.time() - fetched_at if fetched_at else ASIA_TTL_SECS + 1
        if age < ASIA_TTL_SECS:
            asia = cached['asia']
            log.debug('Asia data from DB cache (age %.0fs, TTL %ds)', age, ASIA_TTL_SECS)

    if not asia:
        # Cache stale or empty — fetch fresh via yfinance (handles crumb auth internally)
        fresh = await asyncio.to_thread(_fetch_asia_yfinance)
        if fresh:
            asia = fresh
            await asyncio.to_thread(cache_set, 'global_markets_asia', {
                'asia'      : asia,
                'date'      : today_str,
                'fetched_at': time.time(),
            })
            log.info('Asia data refreshed via yfinance (%d indices)', len(asia))
        elif cached and cached.get('asia'):
            asia = cached['asia']
            log.warning('yfinance failed — serving stale Asia data from DB (date: %s)',
                        cached.get('date', '?'))
        else:
            log.warning('No Asia data: yfinance failed and DB cache is empty')

    return {'asia': asia, 'fx': fx}


# ══════════════════════════════════════════════════════════════════════════════
# GEX  —  Gamma Exposure by strike (Schwab option chain)
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture:
#   DB-tracked (scheduled): SPX, NDX, RUT — baseline 6am ET + 15-min RTH refresh
#   Transient (on-demand):  any other ticker — computed live, 5-min memory cache
#
# Three expiry layers per snapshot:
#   all      — full aggregate across all expirations
#   ex_next  — excluding nearest expiry (removes 0DTE for SPX; nearest weekly for NDX/RUT)
#   monthly  — only 3rd-Friday monthly expirations (structural backdrop)

# Symbols tracked in DB — these are fetched on schedule and served from Supabase
GEX_INDEX_SYMBOLS = ('SPX', 'NDX', 'RUT')

# Transient cache for on-demand symbols (non-index tickers like AMZN)
_gex_transient_cache: dict[str, dict] = {}
_GEX_TRANSIENT_TTL = 300   # 5 minutes

# Schwab requires $ prefix for cash-settled index options
_SCHWAB_INDEX_MAP = {
    'SPX': '$SPX', 'NDX': '$NDX', 'RUT': '$RUT',
    'VIX': '$VIX', 'DJX': '$DJX', 'XSP': '$XSP',
}


def _normalize_gex_symbol(sym: str) -> tuple[str, str]:
    """Return (schwab_symbol, display_symbol). SPX → $SPX etc."""
    upper       = sym.upper().lstrip('$')
    schwab_sym  = _SCHWAB_INDEX_MAP.get(upper, upper)
    display_sym = upper
    return schwab_sym, display_sym


def _is_third_friday(date_str: str) -> bool:
    """Return True if date_str (YYYY-MM-DD) is the 3rd Friday of its month."""
    from datetime import date as _date
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        return False
    if d.weekday() != 4:   # not a Friday
        return False
    friday_count = sum(
        1 for day in range(1, d.day + 1)
        if _date(d.year, d.month, day).weekday() == 4
    )
    return friday_count == 3


def _classify_expiry_type(nearest_date: str, all_dates: list[str] | None = None) -> str:
    """Classify the most significant expiry event this week.

    Looks at the nearest expiry first, then scans the full expiry list for any
    QUARTERLY or MONTHLY event within the next 7 days.  This ensures SPX (which
    has daily expirations so nearest is always 0DTE/1DTE) still surfaces a
    QUARTERLY badge during a Triple Witching week.

    Returns one of:
      'QUARTERLY'  — 3rd Friday of Mar/Jun/Sep/Dec within 7 days (Triple Witching)
      'MONTHLY'    — 3rd Friday of any other month within 7 days
      '0DTE'       — nearest expiry is today
      'WEEKLY'     — nearest is a non-3rd Friday
      'DAILY'      — nearest is a non-Friday (SPX Mon/Tue/Wed/Thu)
    """
    from datetime import date as _date, timedelta
    today = _date.today()

    # Scan all_dates (full chain) for a quarterly/monthly within 7 days — catches
    # SPX where nearest is always 0DTE but a monthly lurks later this week.
    if all_dates:
        cutoff = today + timedelta(days=7)
        for ds in all_dates:
            try:
                d = _date.fromisoformat(ds)
            except ValueError:
                continue
            if today < d <= cutoff and _is_third_friday(ds):
                return 'QUARTERLY' if d.month in (3, 6, 9, 12) else 'MONTHLY'

    # Fall back to nearest-expiry classification
    try:
        d = _date.fromisoformat(nearest_date)
    except ValueError:
        return 'WEEKLY'
    dte = (d - today).days
    if dte == 0:
        return '0DTE'
    if _is_third_friday(nearest_date):
        return 'QUARTERLY' if d.month in (3, 6, 9, 12) else 'MONTHLY'
    if d.weekday() == 4:
        return 'WEEKLY'
    return 'DAILY'


def _is_post_expiry_monday() -> bool:
    """True if today is the Monday immediately after a 3rd-Friday monthly expiry.

    Post-expiry Monday: dealers re-hedge from scratch after expirations wiped
    their gamma book on Friday.  Intraday flows can be erratic and direction
    is hard to read until the new positioning settles (~10:30 AM ET).
    """
    from datetime import date as _date, timedelta
    today = _date.today()
    if today.weekday() != 0:          # not Monday
        return False
    last_friday = today - timedelta(days=3)
    return _is_third_friday(last_friday.isoformat())


def _classify_iv_environment(vix: float | None) -> str:
    """Map VIX level to IV environment label."""
    if vix is None:
        return 'UNKNOWN'
    if vix < 15:
        return 'LOW'
    if vix < 25:
        return 'NORMAL'
    if vix < 35:
        return 'HIGH'
    return 'EXTREME'


def _get_vix() -> float | None:
    """Fetch live VIX from Schwab. Tries $VIX first, falls back to $VIX.X."""
    try:
        from schwab_client import get_quotes
        q = get_quotes(['$VIX', '$VIX.X'])
        val = (q.get('$VIX', {}).get('last')
               or q.get('$VIX.X', {}).get('last'))
        return float(val) if val else None
    except Exception:
        return None


def _gex_strikes_from_maps(
    call_map: dict, put_map: dict,
    include_expiries: set[str], spot: float,
    zero_dte_date: str | None = None,
) -> tuple[dict[float, float], dict[float, float]]:
    """Aggregate call/put GEX by strike for a subset of expiry dates.

    Returns (call_gex_by_strike, put_gex_by_strike) — values in $M.

    zero_dte_date: when set (SPX only), options expiring on this date get an
    adjusted OI = prior_OI + volume/2 to capture same-day 0DTE openings not
    yet reflected in overnight-settled OI.  Applied conservatively: only adds
    the intraday volume estimate on top of existing OI; never replaces it.
    """
    call_gex: dict[float, float] = {}
    put_gex:  dict[float, float] = {}

    def _effective_oi(opt: dict, exp_date: str) -> float:
        oi  = int(opt.get('openInterest') or 0)
        if zero_dte_date and exp_date == zero_dte_date:
            vol = int(opt.get('totalVolume') or 0)
            # volume/2 estimates net new opens (each trade = 1 buyer + 1 seller)
            oi  = oi + vol // 2
        return oi

    for exp_key, strikes_data in call_map.items():
        exp_date = exp_key.split(':')[0]
        if exp_date not in include_expiries:
            continue
        for strike_str, opt_list in strikes_data.items():
            strike = float(strike_str)
            opt    = opt_list[0] if opt_list else {}
            oi     = _effective_oi(opt, exp_date)
            gamma  = opt.get('gamma') or 0
            gex    = oi * gamma * 100 * spot / 1_000_000
            call_gex[strike] = call_gex.get(strike, 0.0) + gex

    for exp_key, strikes_data in put_map.items():
        exp_date = exp_key.split(':')[0]
        if exp_date not in include_expiries:
            continue
        for strike_str, opt_list in strikes_data.items():
            strike = float(strike_str)
            opt    = opt_list[0] if opt_list else {}
            oi     = _effective_oi(opt, exp_date)
            gamma  = opt.get('gamma') or 0
            gex    = oi * gamma * 100 * spot / 1_000_000
            put_gex[strike] = put_gex.get(strike, 0.0) + gex

    return call_gex, put_gex


def _summarize_layer(
    call_gex: dict[float, float],
    put_gex:  dict[float, float],
    spot: float,
    include_strike_rows: bool = False,
) -> dict:
    """Compute summary metrics for one GEX expiry layer.

    Returns: net_gex_mm, gamma_regime, call_wall, put_wall, zero_gamma[, strikes].
    """
    import math as _math
    all_strikes = sorted(set(call_gex) | set(put_gex))
    if not all_strikes:
        return {
            'net_gex_mm': 0.0, 'gamma_regime': 'POSITIVE',
            'call_wall': None, 'put_wall': None, 'zero_gamma': None,
        }

    rows = []
    for s in all_strikes:
        c = call_gex.get(s, 0.0)
        p = put_gex.get(s,  0.0)
        rows.append({
            'strike'      : s,
            'call_gex_mm' : round(c, 4),
            'put_gex_mm'  : round(p, 4),
            'net_gex_mm'  : round(c - p, 4),
            'is_atm'      : abs(s - spot) <= (spot * 0.005),
        })

    total_net  = round(sum(r['net_gex_mm'] for r in rows), 2)

    # Call wall = highest call GEX ABOVE spot (overhead resistance).
    # Put wall  = highest put GEX AT OR BELOW spot (floor support).
    # Cap at ±30% OTM: LEAPS positions far OTM accumulate large notional GEX
    # (massive OI × tiny gamma × high spot) and can dominate the global max,
    # producing walls like $500 when spot is $379 — irrelevant to near-term action.
    _wall_cap  = spot * 0.30
    # Put wall extends 1% above spot to capture ATM puts (e.g. $380 strike when
    # spot is $379.40 is the strongest support — excluding it is too strict).
    _above = [r for r in rows if spot < r['strike'] <= spot + _wall_cap]
    _below = [r for r in rows if spot - _wall_cap <= r['strike'] <= spot * 1.01]
    _best_call = max(_above, key=lambda r: r['call_gex_mm']) if _above else None
    _best_put  = max(_below, key=lambda r: r['put_gex_mm'])  if _below else None
    call_wall  = _best_call['strike'] if _best_call and _best_call['call_gex_mm'] > 0 else None
    put_wall   = _best_put['strike']  if _best_put  and _best_put['put_gex_mm']   > 0 else None

    # Mark walls
    for r in rows:
        r['is_call_wall'] = (call_wall is not None and r['strike'] == call_wall)
        r['is_put_wall']  = (put_wall  is not None and r['strike'] == put_wall)

    # Interpolate zero-gamma flip
    zero_gamma: float | None = None
    prev_s, prev_cum = None, 0.0
    cum = 0.0
    for r in rows:
        cum += r['net_gex_mm']
        if prev_s is not None and (
            (prev_cum < 0 <= cum) or (prev_cum > 0 >= cum)
        ):
            frac = abs(prev_cum) / (abs(prev_cum) + abs(cum))
            zero_gamma = round(prev_s + frac * (r['strike'] - prev_s), 2)
            break
        prev_s, prev_cum = r['strike'], cum

    # Mark zero-gamma on rows
    step = abs(rows[1]['strike'] - rows[0]['strike']) * 0.6 if len(rows) > 1 else 1
    for r in rows:
        r['is_zero_gamma'] = (
            zero_gamma is not None and abs(r['strike'] - zero_gamma) <= step
        )

    result = {
        'net_gex_mm'   : total_net,
        'gamma_regime' : 'POSITIVE' if total_net >= 0 else 'NEGATIVE',
        'call_wall'    : call_wall,
        'put_wall'     : put_wall,
        'zero_gamma'   : zero_gamma,
    }
    if include_strike_rows:
        result['strikes'] = rows
    return result


def _compute_gex(symbol: str, strike_count: int = 60, vix: float | None = None) -> dict:
    """Fetch Schwab option chain and compute GEX across three expiry layers.

    Layers:
      all     — all expirations combined (primary view)
      ex_next — excluding the nearest expiry (removes 0DTE for SPX; nearest weekly for NDX/RUT)
      monthly — only 3rd-Friday monthly expirations (structural backdrop)

    GEX formula: call_gex = +OI × gamma × 100 × spot / 1M
                 put_gex  = same (put gamma contribution)
                 net_gex  = call_gex - put_gex per strike
    """
    import math as _math
    from schwab_client import get_option_chain

    schwab_sym, display_sym = _normalize_gex_symbol(symbol)

    # GEX chain — limited strike range for chart bars (fast, near-ATM only)
    chain = get_option_chain(schwab_sym, strike_count=strike_count)
    spot  = chain.get('underlyingPrice') or 0
    if not spot:
        raise ValueError(f'No underlying price for {schwab_sym}')

    call_map = chain.get('callExpDateMap', {})
    put_map  = chain.get('putExpDateMap',  {})

    # Stats chain — no strike limit so P/C ratio and delta distribution count ALL
    # strikes (matching TOS "Today's Options Statistics"). Index symbols skip this
    # since they have huge chains and GEX data is sufficient.
    is_index_sym = display_sym in _SCHWAB_INDEX_MAP or display_sym in GEX_INDEX_SYMBOLS
    if is_index_sym:
        stats_call_map = call_map
        stats_put_map  = put_map
    else:
        try:
            import logging as _log
            # Pass a very large strikeCount — Schwab caps at their max (all available strikes)
            wide_chain     = get_option_chain(schwab_sym, strike_count=9999)
            wc_calls       = wide_chain.get('callExpDateMap', {})
            wc_puts        = wide_chain.get('putExpDateMap',  {})
            stats_call_map = wc_calls if wc_calls else call_map
            stats_put_map  = wc_puts  if wc_puts  else put_map
            _log.getLogger(__name__).info(
                'wide chain: %d call expiries, %d put expiries',
                len(stats_call_map), len(stats_put_map)
            )
        except Exception as _e:
            _log.getLogger(__name__).warning('wide chain failed (%s), falling back to limited chain', _e)
            stats_call_map = call_map
            stats_put_map  = put_map

    # ── Collect and classify all expiry dates ─────────────────────────────────
    all_dates: list[str] = sorted(
        {exp_key.split(':')[0] for exp_key in list(call_map) + list(put_map)}
    )
    if not all_dates:
        raise ValueError(f'No option expiry dates for {schwab_sym}')

    nearest_date  = all_dates[0]
    monthly_dates = {d for d in all_dates if _is_third_friday(d)}
    ex_next_dates = set(all_dates[1:])           # all except the nearest
    all_dates_set = set(all_dates)

    # SPX 0DTE adjustment: blend in volume/2 for same-day expirations so that
    # intraday 0DTE openings (not yet in overnight OI) are represented in GEX.
    # Only applied to SPX — index with daily expirations where 0DTE flow is largest.
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _today_et = _dt.now(_ZI('America/New_York')).date().isoformat()
    _zero_dte = _today_et if display_sym == 'SPX' else None

    # ── Compute each layer ────────────────────────────────────────────────────
    c_all, p_all         = _gex_strikes_from_maps(call_map, put_map, all_dates_set,  spot, _zero_dte)
    c_exnext, p_exnext   = _gex_strikes_from_maps(call_map, put_map, ex_next_dates,  spot, _zero_dte)
    c_monthly, p_monthly = _gex_strikes_from_maps(
        call_map, put_map,
        monthly_dates if monthly_dates else ex_next_dates,   # fallback if no monthly found
        spot,
        # no 0DTE adjustment for monthly layer — monthly expirations are never same-day
    )

    # ── Nearest-expiry OI walls (primary call_wall / put_wall) ───────────────
    # Uses raw open interest on the nearest expiry only — matches the methodology
    # used by most retail data sources (Yahoo Finance, John Carter, etc.).
    # Multi-expiry GEX aggregation inflates far-OTM LEAPS strikes and produces
    # walls like $500 for MSFT at $379 or $300 puts for AAPL at $298.
    _near_oi_calls: dict[float, int] = {}
    _near_oi_puts:  dict[float, int] = {}
    for _exp_key, _sd in call_map.items():
        if _exp_key.split(':')[0] != nearest_date:
            continue
        for _sk, _ol in _sd.items():
            _s = float(_sk)
            _near_oi_calls[_s] = _near_oi_calls.get(_s, 0) + sum(int(o.get('openInterest') or 0) for o in _ol)
    for _exp_key, _sd in put_map.items():
        if _exp_key.split(':')[0] != nearest_date:
            continue
        for _sk, _ol in _sd.items():
            _s = float(_sk)
            _near_oi_puts[_s] = _near_oi_puts.get(_s, 0) + sum(int(o.get('openInterest') or 0) for o in _ol)

    # 0.5% ATM buffer on put wall to capture strikes fractionally above spot
    # (e.g. $380 when MSFT is at $379.40 is a legitimate ATM put wall).
    _atm_buf   = spot * 0.005
    _cw_cands  = {s: oi for s, oi in _near_oi_calls.items() if s > spot}
    _pw_cands  = {s: oi for s, oi in _near_oi_puts.items()  if s <= spot + _atm_buf}
    _call_wall = max(_cw_cands, key=_cw_cands.__getitem__) if _cw_cands else None
    _put_wall  = max(_pw_cands, key=_pw_cands.__getitem__)  if _pw_cands else None

    layer_all     = _summarize_layer(c_all,     p_all,     spot, include_strike_rows=True)
    layer_exnext  = _summarize_layer(c_exnext,  p_exnext,  spot, include_strike_rows=True)
    layer_monthly = _summarize_layer(c_monthly, p_monthly, spot, include_strike_rows=True)

    # ── Re-stamp strike row flags to match OI-based walls ────────────────────
    # _summarize_layer sets is_call_wall/is_put_wall from GEX; override them so
    # the bar chart highlights the same strikes as the top-level call_wall/put_wall.
    for _row in layer_all.get('strikes', []):
        _row['is_call_wall'] = (_call_wall is not None and _row['strike'] == _call_wall)
        _row['is_put_wall']  = (_put_wall  is not None and _row['strike'] == _put_wall)

    # ── Expected 1-day move from ATM IV ───────────────────────────────────────
    expected_move_pct: float | None = None
    try:
        nearest_key = next(k for k in call_map if k.startswith(nearest_date))
        atm_opts = sorted(
            call_map[nearest_key].items(), key=lambda kv: abs(float(kv[0]) - spot)
        )
        if atm_opts:
            iv = atm_opts[0][1][0].get('volatility') or 0
            if iv > 0:
                expected_move_pct = round(iv / 100 * _math.sqrt(1 / 252) * 100, 2)
    except Exception:
        pass

    # ── DTE for nearest expiry ────────────────────────────────────────────────
    nearest_dte: int | None = None
    try:
        from datetime import date as _date
        nearest_dte = (
            _date.fromisoformat(nearest_date) - _date.today()
        ).days
    except Exception:
        pass

    # ── VIX context ───────────────────────────────────────────────────────────
    if vix is None:
        vix = _get_vix()
    iv_env = _classify_iv_environment(vix)

    # ── P/C ratio + delta distribution (volume-based, matches TOS "Today's Options Statistics") ──
    # Uses totalVolume (today's traded contracts), NOT openInterest (accumulated positions).
    # OI-based P/C reflects structural positioning; volume-based P/C reflects today's activity.
    def _dbucket(d: float) -> str:
        a = abs(d)
        if a <= 0.20: return '0_20'
        if a <= 0.40: return '21_40'
        if a <= 0.60: return '41_60'
        if a <= 0.80: return '61_80'
        return '81_100'

    DBUCKETS = ['0_20', '21_40', '41_60', '61_80', '81_100']
    c_vol_total = p_vol_total = 0
    c_oi_total  = p_oi_total  = 0   # kept for GEX computation reference only
    c_dist: dict[str, int] = {b: 0 for b in DBUCKETS}
    p_dist: dict[str, int] = {b: 0 for b in DBUCKETS}

    for _, strikes_dict in stats_call_map.items():
        for _, opts in strikes_dict.items():
            for opt in opts:
                vol   = int(opt.get('totalVolume') or 0)
                oi    = int(opt.get('openInterest') or 0)
                delta = float(opt.get('delta') or 0)
                c_vol_total += vol
                c_oi_total  += oi
                c_dist[_dbucket(delta)] += oi   # OI-based distribution

    for _, strikes_dict in stats_put_map.items():
        for _, opts in strikes_dict.items():
            for opt in opts:
                vol   = int(opt.get('totalVolume') or 0)
                oi    = int(opt.get('openInterest') or 0)
                delta = float(opt.get('delta') or 0)
                p_vol_total += vol
                p_oi_total  += oi
                p_dist[_dbucket(delta)] += oi   # OI-based distribution

    # Two P/C ratios — each tells a different story:
    # OI  = accumulated positioning (Thursday's snapshot pre-crash; call-heavy from run-up)
    # Vol = today's activity (Friday crash day; put-heavy as traders piled into puts)
    pc_ratio_oi  = round(p_oi_total  / c_oi_total,  3) if c_oi_total  > 0 else None
    pc_ratio_vol = round(p_vol_total / c_vol_total,  3) if c_vol_total > 0 else None
    pc_ratio     = pc_ratio_oi  # keep for backward compat with DB storage

    # ── Top-volume strike per side — nearest expiry only ─────────────────────
    # Only the nearest expiry: a Jan-2027 LEAPS strike with high OI volume
    # is irrelevant to today's regime; we want where traders are active *now*.
    top_vol_call_strike: float | None = None
    top_vol_put_strike:  float | None = None
    try:
        _cvol: dict[float, int] = {}
        for _exp_key, _sd in call_map.items():
            if _exp_key.split(':')[0] != nearest_date:
                continue
            for _sk, _ol in _sd.items():
                _strike = float(_sk)
                _cvol[_strike] = _cvol.get(_strike, 0) + sum(int(o.get('totalVolume') or 0) for o in _ol)
        if _cvol:
            top_vol_call_strike = float(max(_cvol, key=_cvol.__getitem__))
    except Exception:
        pass
    try:
        _pvol: dict[float, int] = {}
        for _exp_key, _sd in put_map.items():
            if _exp_key.split(':')[0] != nearest_date:
                continue
            for _sk, _ol in _sd.items():
                _strike = float(_sk)
                _pvol[_strike] = _pvol.get(_strike, 0) + sum(int(o.get('totalVolume') or 0) for o in _ol)
        if _pvol:
            top_vol_put_strike = float(max(_pvol, key=_pvol.__getitem__))
    except Exception:
        pass

    def _pct(dist: dict, total: int) -> dict:
        return {b: round(dist[b] / total * 100, 1) if total > 0 else 0.0 for b in DBUCKETS}

    delta_distribution = {
        'calls'        : _pct(c_dist, c_oi_total),
        'puts'         : _pct(p_dist, p_oi_total),
        'call_oi'      : c_oi_total,
        'put_oi'       : p_oi_total,
        'pc_ratio_oi'  : pc_ratio_oi,
        'pc_ratio_vol' : pc_ratio_vol,
    }

    # ── Underlying quote context (open, prev close, VWAP) ────────────────────
    underlying_open:       float | None = None
    underlying_prev_close: float | None = None
    underlying_vwap:       float | None = None
    try:
        from schwab_client import get_quotes
        qdata = get_quotes([schwab_sym])
        q = qdata.get(schwab_sym, {}).get('quote', qdata.get(schwab_sym, {}))
        if q.get('openPrice'):
            underlying_open = round(float(q['openPrice']), 2)
        if q.get('closePrice'):
            underlying_prev_close = round(float(q['closePrice']), 2)
    except Exception:
        pass

    # VWAP: computed from 1-min bars — only for equities (index VWAP is meaningless,
    # cash indices have no true volume).  Adds ~1 extra API call for transient symbols.
    is_index_sym = display_sym in _SCHWAB_INDEX_MAP or display_sym in GEX_INDEX_SYMBOLS
    if not is_index_sym:
        try:
            from schwab_client import get_candles
            candles = get_candles(schwab_sym, lookback_days=1, freq_min=1)
            if candles:
                total_pv  = sum((c['high'] + c['low'] + c['close']) / 3 * c['volume'] for c in candles)
                total_vol = sum(c['volume'] for c in candles)
                underlying_vwap = round(total_pv / total_vol, 2) if total_vol > 0 else None
        except Exception:
            pass

    return {
        # Identity & context
        'symbol'             : display_sym,
        'underlying'         : round(spot, 2),
        'vix_ref'            : vix,
        'iv_environment'     : iv_env,
        'nearest_expiry'     : nearest_date,
        'nearest_dte'        : nearest_dte,
        'expiry_type'        : _classify_expiry_type(nearest_date, all_dates),
        'is_post_expiry_monday': _is_post_expiry_monday(),
        'expiries'           : all_dates[:8],

        # All-expiry layer (primary)
        'net_gex_mm'         : layer_all['net_gex_mm'],
        'gamma_regime'       : layer_all['gamma_regime'],
        # Walls: nearest-expiry raw OI (matches retail sources); GEX walls kept as _gex suffix
        'call_wall'          : _call_wall,
        'put_wall'           : _put_wall,
        'call_wall_gex'      : layer_all['call_wall'],
        'put_wall_gex'       : layer_all['put_wall'],
        'zero_gamma'         : layer_all['zero_gamma'],
        'expected_move_pct'  : expected_move_pct,
        'expected_move_pts'  : round(spot * expected_move_pct / 100, 2) if expected_move_pct else None,

        # Ex-next layer
        'net_gex_ex_next_mm' : layer_exnext['net_gex_mm'],
        'call_wall_ex_next'  : layer_exnext['call_wall'],
        'put_wall_ex_next'   : layer_exnext['put_wall'],
        'zero_gamma_ex_next' : layer_exnext['zero_gamma'],

        # Monthly structural layer
        'net_gex_monthly_mm' : layer_monthly['net_gex_mm'],
        'call_wall_monthly'  : layer_monthly['call_wall'],
        'put_wall_monthly'   : layer_monthly['put_wall'],
        'zero_gamma_monthly' : layer_monthly['zero_gamma'],

        # Options flow stats
        'pc_ratio'             : pc_ratio,
        'delta_distribution'   : delta_distribution,
        'underlying_vwap'      : underlying_vwap,
        'underlying_open'      : underlying_open,
        'underlying_prev_close': underlying_prev_close,

        # Strike rows per layer — frontend switches between these for the bar chart
        'strikes'            : layer_all.get('strikes', []),
        'strikes_ex_next'    : layer_exnext.get('strikes', []),
        'strikes_monthly'    : layer_monthly.get('strikes', []),
        'strike_count'       : len(layer_all.get('strikes', [])),

        # Top-volume strikes (crowd focus) — cross-expiry volume argmax per side
        'top_vol_call_strike': top_vol_call_strike,
        'top_vol_put_strike' : top_vol_put_strike,
    }


def _refresh_gex_indices(is_baseline: bool = False) -> bool:
    """Compute GEX for all tracked index symbols and persist to Supabase.

    Called from background_loop:
      - is_baseline=True  at 6am ET  → full OI, authoritative
      - is_baseline=False every 15min during RTH → intraday estimate

    Returns True if at least one symbol had non-zero net GEX (real OI was
    available).  Returns False when all GEX values are zero (Schwab hasn't
    loaded overnight OI yet — baseline should retry rather than mark as done).
    """
    import json as _json
    from db import save_gex_snapshot

    vix = _get_vix()   # fetch once, share across all three symbols
    got_real_data = False

    for sym in GEX_INDEX_SYMBOLS:
        try:
            data = _compute_gex(sym, strike_count=60, vix=vix)
            if data.get('net_gex_mm', 0) != 0:
                got_real_data = True
            row = {
                'symbol'              : sym,
                'is_daily_baseline'   : is_baseline,
                'is_intraday_estimate': not is_baseline,
                'underlying'          : data['underlying'],
                'vix_ref'             : data['vix_ref'],
                'iv_environment'      : data['iv_environment'],
                'net_gex_mm'          : data['net_gex_mm'],
                'gamma_regime'        : data['gamma_regime'],
                'call_wall'           : data['call_wall'],
                'put_wall'            : data['put_wall'],
                'zero_gamma'          : data['zero_gamma'],
                'expected_move_pct'   : data['expected_move_pct'],
                'expected_move_pts'   : data['expected_move_pts'],
                'net_gex_ex_next_mm'  : data['net_gex_ex_next_mm'],
                'call_wall_ex_next'   : data['call_wall_ex_next'],
                'put_wall_ex_next'    : data['put_wall_ex_next'],
                'zero_gamma_ex_next'  : data['zero_gamma_ex_next'],
                'net_gex_monthly_mm'  : data['net_gex_monthly_mm'],
                'call_wall_monthly'   : data['call_wall_monthly'],
                'put_wall_monthly'    : data['put_wall_monthly'],
                'zero_gamma_monthly'  : data['zero_gamma_monthly'],
                'nearest_expiry'      : data['nearest_expiry'],
                'nearest_dte'         : data['nearest_dte'],
                'strikes_json'        : _json.dumps({
                    'all'                : data['strikes'],
                    'ex_next'            : data['strikes_ex_next'],
                    'monthly'            : data['strikes_monthly'],
                    'expiry_dates'       : data.get('expiries', []),
                    'pc_ratio'             : data.get('pc_ratio'),
                    'delta_distribution'   : data.get('delta_distribution'),
                    'underlying_vwap'      : data.get('underlying_vwap'),
                    'underlying_open'      : data.get('underlying_open'),
                    'underlying_prev_close': data.get('underlying_prev_close'),
                }),
            }
            save_gex_snapshot(row)
            log.info('GEX snapshot saved: %s  net=%.1fM  regime=%s  %s',
                     sym, data['net_gex_mm'], data['gamma_regime'],
                     '(BASELINE)' if is_baseline else '(intraday)')
        except Exception as exc:
            log.warning('GEX refresh failed for %s: %s', sym, exc)

    return got_real_data


def _refresh_gex_stocks() -> None:
    """Compute and persist GEX baseline snapshots for all tracked stock symbols.

    Runs at 5:30 PM ET on weekdays (after close, OI still live in Schwab).
    Reads tracked symbols from gex_tracked_symbols table so the list can be
    managed without a redeploy.

    Also fetches OCC market-maker % per expiry and saves to gex_mm_pct for
    future Synthetic OI computation.
    """
    import json as _json
    from db import save_gex_snapshot, get_gex_tracked_symbols, upsert_gex_mm_pct
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    symbols  = get_gex_tracked_symbols()
    vix      = _get_vix()
    today_et = datetime.now(ZoneInfo('America/New_York')).date().isoformat()

    for sym in symbols:
        # ── GEX snapshot (via Schwab, same as index baseline) ──────────────
        try:
            data = _compute_gex(sym, strike_count=100, vix=vix)
            row  = {
                'symbol'              : sym,
                'is_daily_baseline'   : True,
                'is_intraday_estimate': False,
                'underlying'          : data['underlying'],
                'vix_ref'             : data['vix_ref'],
                'iv_environment'      : data['iv_environment'],
                'net_gex_mm'          : data['net_gex_mm'],
                'gamma_regime'        : data['gamma_regime'],
                'call_wall'           : data['call_wall'],
                'put_wall'            : data['put_wall'],
                'zero_gamma'          : data['zero_gamma'],
                'expected_move_pct'   : data['expected_move_pct'],
                'expected_move_pts'   : data['expected_move_pts'],
                'net_gex_ex_next_mm'  : data['net_gex_ex_next_mm'],
                'call_wall_ex_next'   : data['call_wall_ex_next'],
                'put_wall_ex_next'    : data['put_wall_ex_next'],
                'zero_gamma_ex_next'  : data['zero_gamma_ex_next'],
                'net_gex_monthly_mm'  : data['net_gex_monthly_mm'],
                'call_wall_monthly'   : data['call_wall_monthly'],
                'put_wall_monthly'    : data['put_wall_monthly'],
                'zero_gamma_monthly'  : data['zero_gamma_monthly'],
                'nearest_expiry'      : data['nearest_expiry'],
                'nearest_dte'         : data['nearest_dte'],
                'strikes_json'        : _json.dumps({
                    'all'                : data['strikes'],
                    'ex_next'            : data['strikes_ex_next'],
                    'monthly'            : data['strikes_monthly'],
                    'expiry_dates'       : data.get('expiries', []),
                    'pc_ratio'           : data.get('pc_ratio'),
                    'delta_distribution' : data.get('delta_distribution'),
                    'underlying_vwap'    : data.get('underlying_vwap'),
                    'underlying_open'    : data.get('underlying_open'),
                    'underlying_prev_close': data.get('underlying_prev_close'),
                }),
            }
            save_gex_snapshot(row)
            log.info('GEX stock snapshot saved: %s  net=%.1fM  regime=%s',
                     sym, data['net_gex_mm'], data['gamma_regime'])
        except Exception as exc:
            log.warning('GEX stock snapshot failed for %s: %s', sym, exc)
            continue

        # ── OCC market-maker % per expiry (Synthetic OI seed data) ─────────
        try:
            mm_rows = _fetch_occ_mm_pct(sym, today_et)
            if mm_rows:
                upsert_gex_mm_pct(mm_rows)
                log.info('OCC MM%% saved for %s: %d expiries', sym, len(mm_rows))
        except Exception as exc:
            log.warning('OCC MM%% fetch failed for %s: %s', sym, exc)


def _refresh_gex_intraday_stocks() -> None:
    """Compute intraday GEX snapshots for all tracked stock symbols.

    Called every 15 min during RTH alongside the index intraday refresh.
    Skips OCC MM% fetch (daily-only data handled by _refresh_gex_stocks at 5:30 PM).
    """
    import json as _json
    from db import save_gex_snapshot, get_gex_tracked_symbols

    symbols = get_gex_tracked_symbols()
    vix     = _get_vix()

    for sym in symbols:
        try:
            data = _compute_gex(sym, strike_count=100, vix=vix)
            if not data.get('net_gex_mm'):
                continue
            row = {
                'symbol'              : sym,
                'is_daily_baseline'   : False,
                'is_intraday_estimate': True,
                'underlying'          : data['underlying'],
                'vix_ref'             : data['vix_ref'],
                'iv_environment'      : data['iv_environment'],
                'net_gex_mm'          : data['net_gex_mm'],
                'gamma_regime'        : data['gamma_regime'],
                'call_wall'           : data['call_wall'],
                'put_wall'            : data['put_wall'],
                'zero_gamma'          : data['zero_gamma'],
                'expected_move_pct'   : data['expected_move_pct'],
                'expected_move_pts'   : data['expected_move_pts'],
                'net_gex_ex_next_mm'  : data['net_gex_ex_next_mm'],
                'call_wall_ex_next'   : data['call_wall_ex_next'],
                'put_wall_ex_next'    : data['put_wall_ex_next'],
                'zero_gamma_ex_next'  : data['zero_gamma_ex_next'],
                'net_gex_monthly_mm'  : data['net_gex_monthly_mm'],
                'call_wall_monthly'   : data['call_wall_monthly'],
                'put_wall_monthly'    : data['put_wall_monthly'],
                'zero_gamma_monthly'  : data['zero_gamma_monthly'],
                'nearest_expiry'      : data['nearest_expiry'],
                'nearest_dte'         : data['nearest_dte'],
                'strikes_json'        : _json.dumps({
                    'all'     : data.get('strikes', []),
                    'ex_next' : data.get('strikes_ex_next', []),
                    'monthly' : data.get('strikes_monthly', []),
                }),
            }
            save_gex_snapshot(row)
            log.info('GEX intraday stock saved: %s  net=%.1fM  regime=%s',
                     sym, data['net_gex_mm'], data['gamma_regime'])
        except Exception as exc:
            log.warning('GEX intraday stock failed for %s: %s', sym, exc)


def _fetch_occ_mm_pct(symbol: str, snapshot_date: str) -> list[dict]:
    """Fetch market-maker volume % for a symbol from OCC's marketdata API.

    Uses https://marketdata.theocc.com/volume-query — a plain CSV endpoint
    (no Cloudflare protection) that returns per-exchange, per-account-type
    volume for all expirations combined.

    actype values: C=Customer, F=Firm, M=MarketMaker
    porc   values: C=Call, P=Put

    Returns a single-row list (one aggregate row per symbol/date) suitable
    for upsert_gex_mm_pct().  The `expiry` field is set to '9999-01-01' as
    a sentinel meaning "all expirations combined".
    """
    import csv
    import io
    import httpx
    from datetime import date as _date

    d = _date.fromisoformat(snapshot_date)
    report_date_fmt = f'{d.year}{d.month:02d}{d.day:02d}'   # yyyyMMdd

    url = 'https://marketdata.theocc.com/volume-query'
    params = {
        'format'          : 'csv',
        'reportDate'      : report_date_fmt,
        'volumeQueryType' : 'O',       # Options
        'symbolType'      : 'U',       # Underlying
        'symbol'          : symbol,
        'reportType'      : 'D',       # Daily
        'accountType'     : 'ALL',
    }

    try:
        r = httpx.get(url, params=params,
                      headers={'User-Agent': 'Mozilla/5.0'},
                      timeout=20)
        r.raise_for_status()
        text = r.text.strip()
    except Exception as exc:
        log.warning('OCC volume-query failed for %s: %s', symbol, exc)
        return []

    if not text or text.startswith('Report date') or text.startswith('Symbol'):
        log.warning('OCC volume-query no data for %s on %s: %s', symbol, snapshot_date, text[:80])
        return []

    # Parse CSV: quantity, underlying, symbol, actype, porc, exchange, actdate
    call_mm = put_mm = call_total = put_total = 0
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            qty    = int(row.get('quantity', 0) or 0)
            actype = (row.get('actype') or '').strip()
            porc   = (row.get('porc')   or '').strip()
        except (ValueError, KeyError):
            continue
        if porc == 'C':
            call_total += qty
            if actype == 'M':
                call_mm += qty
        elif porc == 'P':
            put_total += qty
            if actype == 'M':
                put_mm += qty

    if call_total == 0 and put_total == 0:
        log.warning('OCC volume-query parsed 0 volume for %s on %s', symbol, snapshot_date)
        return []

    mm_pct_c = round(call_mm / call_total, 4) if call_total else None
    mm_pct_p = round(put_mm  / put_total,  4) if put_total  else None
    log.info('OCC MM%% %s %s: calls %.1f%% (%d/%d)  puts %.1f%% (%d/%d)',
             symbol, snapshot_date,
             (mm_pct_c or 0)*100, call_mm, call_total,
             (mm_pct_p or 0)*100, put_mm,  put_total)

    return [{
        'symbol'        : symbol,
        'snapshot_date' : snapshot_date,
        'expiry'        : '9999-01-01',   # sentinel = all expirations combined
        'mm_pct_calls'  : mm_pct_c,
        'mm_pct_puts'   : mm_pct_p,
        'total_call_vol': call_total,
        'total_put_vol' : put_total,
    }]


# Proxy map — use tracked stock MM% as stand-in for index options
# SPX ↔ SPY, NDX ↔ QQQ (highly correlated dealer behaviour)
_MM_PCT_PROXY: dict[str, str] = {'SPX': 'SPY', 'NDX': 'QQQ'}


def _get_mm_pct(symbol: str) -> dict | None:
    """Return volume-weighted MM% for a symbol (with proxy fallback for indices).

    Checks DB first. If no data exists for this symbol AND it is tracked,
    fetches from OCC inline (one-time cost, result is stored in gex_mm_pct).
    Returns None if data is unavailable or the OCC fetch fails.
    """
    from db import get_mm_pct_for_symbol, get_gex_tracked_symbols, upsert_gex_mm_pct
    from datetime import datetime
    from zoneinfo import ZoneInfo

    lookup = _MM_PCT_PROXY.get(symbol, symbol)

    # 1. Try DB
    mm = get_mm_pct_for_symbol(lookup)
    if mm:
        return mm

    # 2. If the symbol is tracked, fetch from OCC and cache for future calls
    tracked = get_gex_tracked_symbols()
    if lookup not in tracked:
        return None

    try:
        today_et = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
        rows = _fetch_occ_mm_pct(lookup, today_et)
        if rows:
            upsert_gex_mm_pct(rows)
            log.info('OCC MM%% fetched on-demand for %s (%d expiries)', lookup, len(rows))
            return get_mm_pct_for_symbol(lookup)
    except Exception as exc:
        log.warning('On-demand OCC MM%% fetch failed for %s: %s', lookup, exc)

    return None


def _inject_synthetic_gex(data: dict, symbol: str) -> None:
    """Inject dealer GEX fields into a GEX response dict in-place.

    Adds per-strike dealer_call_gex_mm / dealer_put_gex_mm / dealer_net_gex_mm
    and top-level mm_pct_calls / mm_pct_puts / synthetic_net_gex_mm.
    No-ops silently if MM% data is unavailable.
    """
    mm = _get_mm_pct(symbol)
    if not mm:
        return

    mc = mm.get('mm_pct_calls') or 0.0
    mp = mm.get('mm_pct_puts')  or 0.0

    for layer_key in ('strikes', 'strikes_ex_next', 'strikes_monthly'):
        for s in (data.get(layer_key) or []):
            dc = round(s.get('call_gex_mm', 0) * mc, 4)
            dp = round(s.get('put_gex_mm',  0) * mp, 4)
            s['dealer_call_gex_mm'] = dc
            s['dealer_put_gex_mm']  = dp
            s['dealer_net_gex_mm']  = round(dc - dp, 4)

    avg_mm = (mc + mp) / 2
    data['mm_pct_calls']         = mm['mm_pct_calls']
    data['mm_pct_puts']          = mm['mm_pct_puts']
    data['mm_pct_date']          = mm['snapshot_date']
    data['synthetic_net_gex_mm'] = round((data.get('net_gex_mm') or 0.0) * avg_mm, 2)


@app.get('/api/gex/{ticker}')
async def get_gex(ticker: str, strike_count: int = Query(60)):
    """Gamma Exposure (GEX) for any symbol.

    For SPX / NDX / RUT → served from Supabase (latest DB snapshot, seconds old).
    For any other ticker  → computed live from Schwab, 5-min transient cache.

    Response includes three expiry layers:
      primary (all expirations), ex_next (no 0DTE/nearest), monthly (structural).
    """
    _, display_sym = _normalize_gex_symbol(ticker)
    is_index = display_sym in GEX_INDEX_SYMBOLS

    # ── Index symbols: serve from DB ─────────────────────────────────────────
    if is_index:
        try:
            from db import get_latest_gex
            import json as _json
            row = await asyncio.to_thread(get_latest_gex, display_sym)
            if row:
                # Parse strikes_json — new format: {"all": [...], "ex_next": [...], "monthly": [...]}
                # Old format (plain list) treated as all-layer only for backward compat.
                strikes_all, strikes_ex_next, strikes_monthly = [], [], []
                try:
                    raw = _json.loads(row.get('strikes_json') or '{}')
                    if isinstance(raw, list):
                        strikes_all = raw          # legacy single-layer format
                    else:
                        strikes_all     = raw.get('all',     [])
                        strikes_ex_next = raw.get('ex_next', [])
                        strikes_monthly = raw.get('monthly', [])
                    expiry_dates        = raw.get('expiry_dates', []) if isinstance(raw, dict) else []
                    pc_ratio_stored     = raw.get('pc_ratio') if isinstance(raw, dict) else None
                    delta_dist_stored   = raw.get('delta_distribution') if isinstance(raw, dict) else None
                    vwap_stored         = raw.get('underlying_vwap') if isinstance(raw, dict) else None
                    open_stored         = raw.get('underlying_open') if isinstance(raw, dict) else None
                    prev_close_stored   = raw.get('underlying_prev_close') if isinstance(raw, dict) else None
                except Exception:
                    expiry_dates = []
                    pc_ratio_stored = delta_dist_stored = vwap_stored = None
                    open_stored = prev_close_stored = None
                nearest = row.get('nearest_expiry', '')
                # Always serve a live VIX — the stored vix_ref may be stale
                # or absent (weekend baseline skipped).  Fetch is fast (~50ms).
                live_vix = await asyncio.to_thread(_get_vix)
                vix_out  = live_vix or row.get('vix_ref')
                result = {
                    **{k: v for k, v in row.items() if k not in ('strikes_json', 'id')},
                    'strikes'              : strikes_all,
                    'strikes_ex_next'      : strikes_ex_next,
                    'strikes_monthly'      : strikes_monthly,
                    'strike_count'         : len(strikes_all),
                    'vix_ref'              : vix_out,
                    'iv_environment'       : _classify_iv_environment(vix_out),
                    'expiry_type'          : _classify_expiry_type(nearest, expiry_dates or None),
                    'is_post_expiry_monday': _is_post_expiry_monday(),
                    'pc_ratio'             : pc_ratio_stored,
                    'delta_distribution'   : delta_dist_stored,
                    'underlying_vwap'      : vwap_stored,
                    'underlying_open'      : open_stored,
                    'underlying_prev_close': prev_close_stored,
                    'source'               : 'baseline' if row.get('is_daily_baseline') else 'intraday',
                }
                await asyncio.to_thread(_inject_synthetic_gex, result, display_sym)
                return result
        except Exception as exc:
            log.warning('GEX DB fetch failed for %s, falling back to live: %s', display_sym, exc)

    # ── Transient symbols (or DB fallback): compute live ─────────────────────
    cached = _gex_transient_cache.get(display_sym)
    if cached and (time.time() - cached['ts']) < _GEX_TRANSIENT_TTL:
        return {**cached['data'], 'source': 'transient_cache'}

    try:
        data = await asyncio.to_thread(_compute_gex, display_sym, strike_count)
    except Exception as exc:
        log.warning('GEX compute error for %s: %s', display_sym, exc)
        return JSONResponse({'error': str(exc)}, status_code=500)

    data['source'] = 'live'
    await asyncio.to_thread(_inject_synthetic_gex, data, display_sym)
    _gex_transient_cache[display_sym] = {'data': data, 'ts': time.time()}
    return data


# ── Earnings Calendar (Alpha Vantage) ─────────────────────────────────────────
# Fetched once per day; cached in memory. One API call covers all symbols.

_earnings_cache: dict = {}   # {'date': str, 'events': dict[str, str]}  ticker → reportDate


def _refresh_earnings_calendar() -> dict[str, str]:
    """Fetch 3-month earnings calendar from Alpha Vantage. Returns {ticker: reportDate}."""
    import os, csv, io, requests as _req
    key = os.environ.get('ALPHA_VANTAGE_API_KEY', '')
    if not key:
        return {}
    try:
        resp = _req.get('https://www.alphavantage.co/query', params={
            'function': 'EARNINGS_CALENDAR',
            'horizon' : '3month',
            'apikey'  : key,
        }, timeout=20)
        if not resp.ok:
            return {}
        reader = csv.DictReader(io.StringIO(resp.text))
        return {row['symbol']: row['reportDate'] for row in reader if row.get('symbol') and row.get('reportDate')}
    except Exception as exc:
        log.warning('Earnings calendar fetch failed: %s', exc)
        return {}


def _get_earnings_calendar() -> dict[str, str]:
    """Return cached {ticker: reportDate}, refreshing once per day."""
    from datetime import date
    today = date.today().isoformat()
    if _earnings_cache.get('date') != today:
        _earnings_cache['events'] = _refresh_earnings_calendar()
        _earnings_cache['date']   = today
        log.info('Earnings calendar refreshed: %d upcoming events', len(_earnings_cache['events']))
    return _earnings_cache.get('events', {})


def earnings_proximity(ticker: str, window_days: int = 5) -> dict:
    """Check if ticker has earnings within window_days trading days.

    Returns: {near: bool, report_date: str|None, days_away: int|None}
    """
    from datetime import date, timedelta
    calendar = _get_earnings_calendar()
    report   = calendar.get(ticker.upper())
    if not report:
        return {'near': False, 'report_date': None, 'days_away': None}
    try:
        rd       = date.fromisoformat(report)
        today    = date.today()
        cal_days = (rd - today).days
        # Approximate trading days (rough: 5/7 of calendar days)
        trading_days = max(0, int(cal_days * 5 / 7))
        return {
            'near'       : 0 <= trading_days <= window_days,
            'report_date': report,
            'days_away'  : trading_days,
        }
    except ValueError:
        return {'near': False, 'report_date': report, 'days_away': None}


@app.post('/api/gex/refresh-stocks')
async def manual_refresh_gex_stocks(force: bool = False):
    """Manually trigger the 5:30 PM GEX baseline for all tracked stock symbols.
    Useful after adding new symbols mid-day or when the scheduled run is missed.
    Blocked on weekends and market holidays (Schwab returns zero OI) unless force=true.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_et  = datetime.now(ZoneInfo('America/New_York'))
    weekday = now_et.weekday()   # 0=Mon … 6=Sun
    if not force and weekday >= 5:
        return JSONResponse(
            {'status': 'skipped', 'reason': 'Weekend — Schwab has no OI data. Use ?force=true to override.'},
            status_code=200,
        )
    try:
        await asyncio.to_thread(_refresh_gex_stocks)
        from db import get_gex_tracked_symbols
        symbols = await asyncio.to_thread(get_gex_tracked_symbols)
        return {'status': 'ok', 'symbols_refreshed': symbols}
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


@app.get('/api/earnings-calendar')
async def get_earnings_calendar(days: int = Query(14)):
    """Return all S&P 500 / NASDAQ 100 symbols reporting earnings within `days` trading days."""
    from datetime import date
    calendar = await asyncio.to_thread(_get_earnings_calendar)
    today    = date.today()
    results  = []
    for ticker, report_date in calendar.items():
        try:
            rd           = date.fromisoformat(report_date)
            trading_days = max(0, int(((rd - today).days) * 5 / 7))
            if 0 <= trading_days <= days:
                results.append({'symbol': ticker, 'report_date': report_date, 'days_away': trading_days})
        except ValueError:
            continue
    results.sort(key=lambda r: r['report_date'])
    return {'total': len(results), 'window_days': days, 'events': results}


@app.post('/api/kill-filter')
async def kill_filter(symbols: list[str] = Body(...)):
    """Carter kill filter: gamma flip check for a list of equity symbols.

    For each symbol, fetches the option chain (or serves from 5-min transient
    cache), computes net GEX across all expirations, and determines whether
    spot is above or below the gamma flip level.

    Pass  = POSITIVE gamma regime (spot > flip)  → debit calls work
    Kill  = NEGATIVE gamma regime (spot < flip)  → dealer amplifier, skip

    Also flags thin GEX footprint (<$1M net) as a soft warning even when
    regime is POSITIVE — matches Carter's IVZ exclusion logic.
    """
    MIN_FOOTPRINT_MM = 1.0   # $1M net GEX minimum

    async def _check(sym: str) -> dict:
        upper = sym.upper().strip()
        try:
            cached = _gex_transient_cache.get(upper)
            if cached and (time.time() - cached['ts']) < _GEX_TRANSIENT_TTL:
                data = cached['data']
            else:
                data = await asyncio.to_thread(_compute_gex, upper, 9999)
                _gex_transient_cache[upper] = {'data': data, 'ts': time.time()}

            spot     = data.get('underlying', 0)
            net_gex  = data.get('net_gex_mm', 0)
            regime   = data.get('gamma_regime', 'UNKNOWN')
            flip     = data.get('zero_gamma')

            gap      = round(spot - flip, 2) if (spot and flip) else None
            gap_pct  = round(gap / spot * 100, 2) if (gap is not None and spot) else None
            thin     = abs(net_gex) < MIN_FOOTPRINT_MM
            passed   = regime == 'POSITIVE' and not thin

            gap_str  = f'{gap_pct:+.1f}%' if gap_pct is not None else 'n/a'
            if regime == 'NEGATIVE':
                verdict = f'KILL — negative gamma (flip ${flip}, spot ${spot}, gap {gap_str})'
            elif thin:
                verdict = f'KILL — thin footprint (net GEX ${net_gex:.2f}M < $1M)'
            else:
                verdict = f'PASS — positive gamma (flip ${flip}, spot ${spot}, gap {gap_str})'

            return {
                'symbol'      : upper,
                'pass'        : passed,
                'regime'      : regime,
                'spot'        : spot,
                'flip'        : flip,
                'gap'         : gap,
                'gap_pct'     : gap_pct,
                'net_gex_mm'  : round(net_gex, 2),
                'thin'        : thin,
                'verdict'     : verdict,
            }
        except Exception as exc:
            return {'symbol': upper, 'pass': False, 'regime': 'ERROR', 'verdict': str(exc)}

    results = await asyncio.gather(*[_check(s) for s in symbols])
    passed  = [r for r in results if r.get('pass')]
    killed  = [r for r in results if not r.get('pass')]
    return {
        'total'  : len(results),
        'passed' : len(passed),
        'killed' : len(killed),
        'results': list(results),
    }


@app.get('/api/gex/{ticker}/history')
async def get_gex_history(ticker: str, hours: int = Query(8)):
    """Return intraday GEX history for a tracked index symbol (SPX/NDX/RUT).
    Used to show how call wall / put wall moved through the session.
    """
    _, display_sym = _normalize_gex_symbol(ticker)
    if display_sym not in GEX_INDEX_SYMBOLS:
        return JSONResponse({'error': 'History only available for SPX, NDX, RUT'}, status_code=400)
    try:
        from db import get_gex_history as _get_hist
        rows = await asyncio.to_thread(_get_hist, display_sym, hours)
        return {'symbol': display_sym, 'hours': hours, 'rows': rows}
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


@app.get('/api/market-regime')
async def get_market_regime(force: bool = Query(False)):
    """GEX-based Market Regime snapshot for all tracked symbols.

    Derives REGIME, FLOW, MAGNET, MAX GEX from the latest gex_snapshots rows.
    Indices (SPX/NDX/RUT) are served from DB (updated every 15 min during RTH).
    Tracked stocks are served from DB (updated at 5:30 PM baseline).
    Pass ?force=true to trigger a live Schwab compute regardless of RTH / cache age.
    """
    import json as _json
    from db import get_latest_gex, get_gex_tracked_symbols, save_gex_snapshot

    try:
        tracked = await asyncio.to_thread(get_gex_tracked_symbols)
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)

    # Indices first, then tracked stocks (exclude duplicates)
    ordered = list(GEX_INDEX_SYMBOLS) + [s for s in tracked if s not in GEX_INDEX_SYMBOLS]

    from datetime import datetime, timezone

    def _snapshot_age_s(row: dict) -> float:
        """Seconds since the snapshot was captured. Returns inf if unknown."""
        try:
            return (datetime.now(timezone.utc) -
                    datetime.fromisoformat(row['captured_at'].replace('Z', '+00:00'))
                   ).total_seconds()
        except Exception:
            return float('inf')

    def _build_db_row(sym: str, live: dict) -> dict:
        return {
            'symbol'              : sym,
            'is_daily_baseline'   : False,
            'is_intraday_estimate': True,
            'underlying'          : live['underlying'],
            'net_gex_mm'          : live['net_gex_mm'],
            'gamma_regime'        : live['gamma_regime'],
            'call_wall'           : live['call_wall'],
            'put_wall'            : live['put_wall'],
            'zero_gamma'          : live['zero_gamma'],
            'vix_ref'             : live.get('vix_ref'),
            'iv_environment'      : live.get('iv_environment'),
            'expected_move_pct'   : live.get('expected_move_pct'),
            'expected_move_pts'   : live.get('expected_move_pts'),
            'net_gex_ex_next_mm'  : live.get('net_gex_ex_next_mm'),
            'call_wall_ex_next'   : live.get('call_wall_ex_next'),
            'put_wall_ex_next'    : live.get('put_wall_ex_next'),
            'zero_gamma_ex_next'  : live.get('zero_gamma_ex_next'),
            'net_gex_monthly_mm'  : live.get('net_gex_monthly_mm'),
            'call_wall_monthly'   : live.get('call_wall_monthly'),
            'put_wall_monthly'    : live.get('put_wall_monthly'),
            'zero_gamma_monthly'  : live.get('zero_gamma_monthly'),
            'nearest_expiry'      : live.get('nearest_expiry'),
            'nearest_dte'         : live.get('nearest_dte'),
            'strikes_json'        : _json.dumps({
                'all'                : live.get('strikes', []),
                'ex_next'            : live.get('strikes_ex_next', []),
                'monthly'            : live.get('strikes_monthly', []),
                'top_vol_call_strike': live.get('top_vol_call_strike'),
                'top_vol_put_strike' : live.get('top_vol_put_strike'),
            }),
        }

    import time as _time

    # RTH check — only attempt live Schwab computes during market hours.
    # Outside RTH, Schwab returns zero OI; we must not overwrite valid DB snapshots.
    # ?force=true bypasses this for manual refresh (e.g. the Refresh button).
    _now_et   = datetime.now(ET)
    _et_min   = _now_et.hour * 60 + _now_et.minute
    _is_rth   = _now_et.weekday() < 5 and (9 * 60 + 30) <= _et_min < 16 * 60
    _can_live = _is_rth or force   # force lets the Refresh button always pull live
    # During RTH or force: treat DB as stale after 5 min → live compute.
    # Outside RTH (auto-refresh): infinity = never trigger live, serve last snapshot.
    _stale_secs = 5 * 60 if _can_live else float('inf')

    async def _resolve_sym(sym: str) -> tuple[str, dict | None]:
        """Return (sym, best_row).

        Priority order:
        1. Transient cache (same live data the GEX panel uses) — always fresh
        2. DB snapshot — if age < _stale_secs return as-is
        3. Live Schwab compute (RTH only, when DB is stale) — saves result to DB
        """
        r = None
        try:
            # 1. Check transient cache first — non-index symbols live here
            cached = _gex_transient_cache.get(sym)
            if cached and (_time.time() - cached['ts']) < _GEX_TRANSIENT_TTL:
                d = cached['data']
                if d.get('net_gex_mm') != 0:
                    return sym, {
                        'symbol'        : sym,
                        'underlying'    : d.get('underlying'),
                        'net_gex_mm'    : d.get('net_gex_mm'),
                        'gamma_regime'  : d.get('gamma_regime'),
                        'call_wall'     : d.get('call_wall'),
                        'put_wall'      : d.get('put_wall'),
                        'zero_gamma'    : d.get('zero_gamma'),
                        'iv_environment': d.get('iv_environment'),
                        'captured_at'   : datetime.fromtimestamp(cached['ts'], tz=timezone.utc).isoformat(),
                        'strikes_json'  : _json.dumps({
                            'all'                : d.get('strikes', []),
                            'top_vol_call_strike': d.get('top_vol_call_strike'),
                            'top_vol_put_strike' : d.get('top_vol_put_strike'),
                        }),
                    }

            # 2. DB snapshot
            r = await asyncio.to_thread(get_latest_gex, sym, True)
            stale = force or (r is None) or _snapshot_age_s(r) > _stale_secs
            if stale and _can_live:
                # 3. Live compute — only persist to DB during RTH to protect
                # last-trading-day snapshots from weekend/holiday Schwab data.
                live = await asyncio.to_thread(_compute_gex, sym, 100)
                if live and live.get('net_gex_mm') != 0:
                    db_row = _build_db_row(sym, live)
                    await asyncio.to_thread(save_gex_snapshot, db_row)
                    _gex_transient_cache[sym] = {'data': live, 'ts': _time.time()}
                    r = {**db_row, 'captured_at': datetime.now(timezone.utc).isoformat()}
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning('market-regime resolve %s: %s', sym, _e)
        return sym, r

    # Resolve all symbols in parallel — one Schwab call per stale symbol, all concurrent
    results = await asyncio.gather(*[_resolve_sym(s) for s in ordered])
    all_gex: dict = {sym: row for sym, row in results if row}

    rows = []
    for sym in ordered:
        row = all_gex.get(sym)
        if not row:
            continue

        # Parse strikes_json for prev_close + strike rows for FLOW
        prev_close   = None
        strikes_list = []
        pc_ratio     = None   # kept for backward compat in response payload
        top_vol_call_strike: float | None = None
        top_vol_put_strike:  float | None = None
        try:
            sj = row.get('strikes_json')
            # jsonb column returns a string when stored via json.dumps(); parse it
            raw = _json.loads(sj) if isinstance(sj, str) else (sj or {})
            if isinstance(raw, dict):
                prev_close          = raw.get('underlying_prev_close')
                strikes_list        = raw.get('all', [])
                top_vol_call_strike = raw.get('top_vol_call_strike')
                top_vol_put_strike  = raw.get('top_vol_put_strike')
            elif isinstance(raw, list):
                strikes_list = raw   # legacy single-layer format
        except Exception:
            pass

        spot      = row.get('underlying') or 0
        net_gex   = row.get('net_gex_mm') or 0
        gr        = row.get('gamma_regime', 'POSITIVE')
        call_wall = row.get('call_wall')
        put_wall  = row.get('put_wall')
        zero_gam  = row.get('zero_gamma')

        # Day % change
        day_pct = None
        if spot and prev_close and prev_close > 0:
            day_pct = round((spot - prev_close) / prev_close * 100, 2)

        # REGIME — indices vs stocks have different GEX magnitude ranges
        is_index        = sym in GEX_INDEX_SYMBOLS
        heavy_threshold = 500 if is_index else 50
        chop_threshold  = 50  if is_index else 5

        if abs(net_gex) <= chop_threshold:
            regime_label = 'Chop'
        elif gr == 'NEGATIVE':
            going_up = day_pct is not None and day_pct > 0
            suffix   = ' + Heavy Hedges' if net_gex < -heavy_threshold else ''
            regime_label = ('Trend Up' if going_up else 'Trend Down') + suffix
        else:
            regime_label = 'Pinned'

        # FLOW — GEX-weighted put/call ratio from the strike rows.
        # Uses total call GEX vs total put GEX rather than raw OI count;
        # more reliable (OI-based pc_ratio is unavailable after Schwab clears OI at close).
        flow = 'n/a'
        if strikes_list:
            total_call = sum(abs(s.get('call_gex_mm') or 0) for s in strikes_list)
            total_put  = sum(abs(s.get('put_gex_mm')  or 0) for s in strikes_list)
            if total_call > 0:
                gex_ratio = total_put / total_call
                if gex_ratio >= 1.3:
                    flow = 'BEAR'
                elif gex_ratio >= 1.1:
                    flow = 'MIXED'
                elif gex_ratio <= 0.85:
                    flow = 'BULL'
                else:
                    flow = 'QUIET'

        # MAX GEX — highest gamma concentration strike (= call_wall; call gamma > put gamma at that strike)
        max_gex_strike = call_wall

        # MAGNET — zero_gamma is the key flip level price gravitates toward in negative regimes
        magnet = None
        magnet_target = zero_gam or max_gex_strike
        if magnet_target and spot:
            dist_pct  = round((magnet_target - spot) / spot * 100, 2)
            direction = 'UP' if magnet_target > spot else 'DN'
            magnet    = {'direction': direction, 'pct': round(abs(dist_pct), 2), 'target': magnet_target}

        rows.append({
            'symbol'              : sym,
            'spot'                : spot,
            'day_pct'             : day_pct,
            'regime'              : regime_label,
            'gamma_regime'        : gr,
            'flow'                : flow,
            'pc_ratio'            : pc_ratio,
            'max_gex'             : max_gex_strike,
            'call_wall'           : call_wall,
            'put_wall'            : put_wall,
            'zero_gamma'          : zero_gam,
            'magnet'              : magnet,
            'net_gex_mm'          : net_gex,
            'captured_at'         : row.get('captured_at'),
            'iv_environment'      : row.get('iv_environment'),
            'top_vol_call_strike' : top_vol_call_strike,
            'top_vol_put_strike'  : top_vol_put_strike,
        })

    return {'rows': rows, 'count': len(rows)}


@app.get('/api/global-markets')
async def get_global_markets():
    """Asian market daily close change % and FX risk-on/off pairs.
    Asia: Yahoo Finance v8 direct API, DB-cached (30 min during Asian session, daily otherwise).
    FX: live from Schwab, memory-cached 15 min.
    Background loop proactively refreshes both — this endpoint usually serves from cache."""
    now = time.time()
    cached_data = _GLOBAL_MARKETS_CACHE.get('data', {})
    cache_age   = now - _GLOBAL_MARKETS_CACHE.get('ts', 0)

    # Serve memory cache for FX within the TTL window (Asia is always DB-backed)
    if cached_data and cache_age < _GLOBAL_MARKETS_TTL:
        return cached_data

    data = await _refresh_global_markets()

    # Cache in memory whenever we have at least FX data
    if data.get('fx') or data.get('asia'):
        _GLOBAL_MARKETS_CACHE['data'] = data
        _GLOBAL_MARKETS_CACHE['ts']   = now
    return data


def _sr_summary_for_chat(ticker: str, sr: dict) -> str:
    """Format Fibonacci S/R as compact context for Claude."""
    if 'error' in sr:
        return f'{ticker}: no S/R data'
    res   = sr.get('resistance', [])[:4]
    sup   = sr.get('support',    [])[:4]
    price = sr.get('current_price', 0)
    fh    = sr.get('fib_high', 0)
    fl    = sr.get('fib_low',  0)
    lines = [f'{ticker} Fib S/R (range {fl}–{fh}, price={price}):']
    if res:
        lines.append('  Resistance (broken, above): ' + '  |  '.join(
            f"{r['price']} ({r['zone_type']}, {r['dist_pct']:+.1f}%)" for r in res
        ))
    if sup:
        lines.append('  Support (floor, below): ' + '  |  '.join(
            f"{s['price']} ({s['zone_type']}, {s['dist_pct']:+.1f}%)" for s in sup
        ))
    return '\n'.join(lines)


# Known stock tickers (used to detect ticker mentions in chat messages)
_KNOWN_TICKERS: set[str] = set()

def _load_known_tickers():
    global _KNOWN_TICKERS
    try:
        rows = get_etf_holding_tickers()
        _KNOWN_TICKERS = set(t.upper() for t in rows)
    except Exception:
        pass

# Common English words to exclude from ticker detection
_STOP_WORDS = {
    'I', 'A', 'AN', 'THE', 'AND', 'OR', 'BUT', 'FOR', 'NOR', 'SO', 'YET',
    'AT', 'BY', 'IN', 'OF', 'ON', 'TO', 'UP', 'AS', 'IS', 'IT', 'BE',
    'DO', 'GO', 'IF', 'MY', 'NO', 'US', 'WE', 'ME', 'HE', 'SHE', 'HIM',
    'HER', 'HIS', 'ITS', 'OUR', 'YOU', 'ARE', 'WAS', 'HAS', 'HAD', 'DID',
    'CAN', 'MAY', 'ALL', 'ANY', 'NOW', 'NEW', 'OLD', 'BIG', 'LOW', 'HIGH',
    'GET', 'SET', 'LET', 'PUT', 'RUN', 'SEE', 'HOW', 'WHY', 'WHAT', 'WHEN',
    'WHERE', 'WILL', 'WITH', 'FROM', 'INTO', 'THAN', 'THEN', 'THIS', 'THAT',
    'THEY', 'THEM', 'THEIR', 'THERE', 'BEEN', 'HAVE', 'DOES', 'JUST', 'ALSO',
    'MOST', 'SOME', 'MANY', 'MORE', 'VERY', 'WELL', 'LOOK', 'LIKE', 'OVER',
    'LAST', 'NEXT', 'LONG', 'LIVE', 'MAKE', 'GIVE', 'SHOW', 'TELL', 'NEAR',
    'BEST', 'GOOD', 'STOP', 'SELL', 'BOTH', 'WEEK', 'YEAR', 'TIME', 'BACK',
    'BULL', 'BEAR', 'OPEN', 'HOLD', 'RISK', 'SIDE', 'PLAN', 'MOVE', 'PULL',
    'PUSH', 'FIND', 'CALL', 'SAID', 'DATA', 'WANT', 'NEED', 'SAME', 'EACH',
    'MUCH', 'SUCH', 'ONLY', 'EVEN', 'DOWN', 'AWAY', 'ONCE', 'TOOK', 'KEEP',
    'CHART', 'LEVEL', 'PRICE', 'TRADE', 'STOCK', 'SETUP', 'BREAK', 'ABOVE',
    'BELOW', 'ENTRY', 'SHORT', 'FIRST', 'AFTER', 'ABOUT', 'WHICH', 'THESE',
    'THOSE', 'BEING', 'DOING', 'GOING', 'THINK', 'USING', 'BASED', 'WATCH',
    'TODAY', 'DAILY', 'INTRA', 'SWING', 'TREND', 'SMART', 'CLEAN', 'QUICK',
    # Extra common words that happen to match real tickers
    'WAY', 'KEY', 'ACT', 'ADD', 'AGO', 'AIM', 'ASK', 'BID', 'BOT', 'BUY',
    'DAY', 'END', 'FED', 'FEW', 'FIB', 'FIX', 'GAP', 'HIT', 'HOD', 'LOD',
    'LOT', 'MID', 'MIN', 'MAX', 'OFF', 'OUT', 'PAY', 'POC', 'POP', 'PRE',
    'RAW', 'SAY', 'TAP', 'TRY', 'TWO', 'USE', 'VIX', 'VOL', 'WIN', 'YES',
    # VBH / trading system terms — never stock tickers
    'VBH', 'AGG', 'CON', 'WIDE', 'NEAR', 'MACD', 'MOMO', 'TICK', 'TRIN',
    'RTH', 'ETH', 'IB', 'VAH', 'VAL', 'VWAP', 'MTF', 'ATH', 'POS', 'NEG',
    'TF', 'SQ', 'SL', 'TP', 'RR', 'TA', 'PA', 'HH', 'LL', 'HL', 'LH',
    'CONF', 'CAUT', 'NGTD', 'NEUT', 'ENTRY', 'FIRED', 'BULL', 'BEAR',
    'SQUEEZE', 'SIGNAL', 'MODEL', 'BIAS', 'ZONE',
}

def _detect_tickers_in_message(msg: str) -> list[str]:
    """Extract stock tickers from message.

    Key rules:
    1. Only match words that are ALREADY uppercase in the original message.
       This prevents common words like 'way', 'why', 'that' from being
       detected after msg.upper() — the user must write 'AAPL' not 'aapl'.
    2. Exclude stems of futures symbols so '/ES' never produces the stock 'ES'.
       Any /TICKER pattern in the message is added to the exclusion set.
    3. Known holdings (from DB) take priority over generic word matching.
    """
    import re

    # Build exclusion set: stems of all known futures symbols
    # e.g. /ES → 'ES', /NQ → 'NQ' — these are NOT stocks
    futures_roots = {f.lstrip('/') for f in {'/ES', '/NQ', '/YM', '/RTY', '/GC'}}
    # Also catch any /TICKER pattern the user typed (e.g. "/CL", "/ZC")
    for m in re.finditer(r'/([A-Z]{2,5})', msg.upper()):
        futures_roots.add(m.group(1))

    # Only extract words that are uppercase IN THE ORIGINAL message.
    # This is the critical fix: 'way' → not matched; 'WAY' → matched.
    words = re.findall(r'\b([A-Z]{2,5})\b', msg)   # original case, not .upper()

    # Priority 1: words in known holdings set (explicit stock tickers from DB)
    known = [w for w in words
             if w.upper() in _KNOWN_TICKERS
             and w.upper() not in futures_roots
             and w.upper() not in _STOP_WORDS]

    # Priority 2: other uppercase words that look like tickers
    unknown = [w for w in words
               if w.upper() not in _KNOWN_TICKERS
               and w.upper() not in futures_roots
               and w.upper() not in _STOP_WORDS
               and 2 <= len(w) <= 5]

    # Deduplicate preserving order, known first, cap at 5
    seen: list[str] = []
    for w in known + unknown:
        wu = w.upper()
        if wu not in seen:
            seen.append(wu)
    return seen[:5]


@app.post('/api/ai/chat')
async def ai_chat(body: dict = Body(...)):
    """Interactive chat with live futures + on-demand S/R context."""
    message = body.get('message', '').strip()
    history = body.get('history', [])

    if not message:
        return {'reply': ''}

    # Fetch futures snapshot
    tasks   = [_fetch_agent_symbol_data(sym) for sym in AGENT_FUTURES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid   = [r for r in results if isinstance(r, dict)]
    futures_ctx = _build_claude_prompt(valid) if valid else 'Futures data unavailable.'

    # Detect stock tickers mentioned in message → fetch S/R for each
    if not _KNOWN_TICKERS:
        _load_known_tickers()
    mentioned = _detect_tickers_in_message(message)
    sr_ctx = ''
    sr_parts: list[dict] = []

    async def _fetch_sr_with_fallback(ticker: str) -> dict:
        """Fetch Fib S/R using daily candles (90d range). Cache shared with /api/sr."""
        cached = _fib_cache_get(ticker)
        if cached:
            cached['days'] = 90
            return cached
        daily_bars = []
        try:
            raw_daily  = await asyncio.to_thread(get_daily_candles, ticker, 90)
            daily_bars = [
                {
                    'bar_time': datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).isoformat(),
                    'high'    : float(c['high']),
                    'low'     : float(c['low']),
                    'close'   : float(c['close']),
                    'open'    : float(c['open']),
                }
                for c in raw_daily
            ]
        except Exception as e:
            log.debug('Chat SR daily %s: %s', ticker, e)

        if not daily_bars:
            # Fallback: stored 1-min bars or fresh Schwab fetch
            daily_bars = await asyncio.to_thread(get_ticker_candles, ticker, 30)
            if not daily_bars:
                try:
                    raw  = await asyncio.to_thread(get_candles, ticker, 3, 1)
                    daily_bars = _candles_to_ticker_rows(ticker, raw)
                    if daily_bars:
                        await asyncio.to_thread(upsert_ticker_candles, daily_bars)
                except Exception as e:
                    log.debug('Chat SR fallback %s: %s', ticker, e)

        sr = _compute_fib_sr(daily_bars)
        sr['days'] = 90
        if daily_bars:
            _fib_cache_set(ticker, sr)
        return sr

    if mentioned:
        sr_parts = list(await asyncio.gather(*[_fetch_sr_with_fallback(t) for t in mentioned]))
        sr_lines = []
        for ticker, sr in zip(mentioned, sr_parts):
            sr_lines.append(_sr_summary_for_chat(ticker, sr))
        sr_ctx = '\n\nSTOCK S/R DATA (from intraday 1-min bars, live Schwab):\n' + '\n'.join(sr_lines)

    try:
        import anthropic
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {'reply': 'ANTHROPIC_API_KEY not configured.'}

        client   = anthropic.Anthropic(api_key=api_key)
        messages = [{'role': m['role'], 'content': m['content']} for m in history if m.get('role') in ('user', 'assistant')]
        messages.append({'role': 'user', 'content': message})

        sr_note = (
            f'{sr_ctx}\n'
            if sr_ctx else
            '\n(No stocks mentioned — futures data only. S/R auto-fetches when user mentions a ticker.)\n'
        )
        resp = client.messages.create(
            model      = 'claude-haiku-4-5',
            max_tokens = 700,
            system     = (
                'You are a concise trading assistant with access to live futures and intraday stock S/R data.\n\n'
                f'LIVE FUTURES DATA:\n{futures_ctx}\n'
                f'{sr_note}\n'
                'RESPONSE FORMAT RULES (strictly follow):\n'
                '- NEVER reproduce tables, bullet lists of levels, or raw price data — the UI already shows that visually\n'
                '- Write ONLY the trading narrative: what to do, which level matters most, and why\n'
                '- 2-4 sentences maximum. Be direct. No headers, no markdown tables.\n'
                '- Reference prices inline naturally: "buy dips to 146.95" not a table row\n'
                '- For S/R: name the 1-2 most important levels and the trade idea around them\n'
                '- For futures: state bias + key level to watch + one action'
            ),
            messages   = messages,
        )
        # Build S/R data payload for frontend display
        sr_data = {}
        for ticker, sr in zip(mentioned, sr_parts if mentioned else []):
            sr['days'] = 3
            sr_data[ticker] = {
                'ticker'       : ticker,
                'current_price': sr.get('current_price', 0),
                'resistance'   : sr.get('resistance', [])[:5],
                'support'      : sr.get('support', [])[:5],
            }

        return {'reply': resp.content[0].text, 'sr_data': sr_data}
    except Exception as e:
        log.warning('Claude chat error: %s', e)
        return {'reply': f'Error: {e}', 'sr_data': {}}


# ── Gemini Ask AI (corner chat) ───────────────────────────────────────────────

_ASK_AI_SYSTEM = """\
You are an experienced trading assistant covering futures, stocks, ETFs, and macro. \
You have access to live market data including VBH mean-reversion signals, market internals, \
sector ETF performance, Market Profile analysis, and general market knowledge.

SCOPE: Answer ANY market question — futures, stocks, ETFs, macro, sectors, earnings, technicals, \
Market Profile, Dalton methodology. \
NEVER say "we don't track that", "not in our system", "not a futures contract", or any variation. \
NEVER mention VBH, signal system, or dashboard limitations to the user. \
Just answer directly like a trader who knows markets — use sector context, macro, technicals, \
fundamentals, and the live data you have.

HOW TO ANALYZE — follow this order:
1. INTERNALS FIRST: $TICK extremes (>+800 = strong bull, <-800 = strong bear) override everything. \
$TRIN <0.8 = buying volume dominant, >1.5 = selling volume dominant. A/D ratio shows breadth.
2. SECTOR LEADERSHIP: XLK leads /NQ (Nasdaq). XLV + XLF lead /YM (Dow). \
XLK + XLF + XLV drive /ES (S&P). XLF + XLI lead /RTY (Russell). \
If the leading sector is weak, the futures will be weak — that is the CAUSE.
3. MARKET PROFILE: Use the live IB score, zone status, and current bias as structural context. \
A BEARISH IB with CONFIRMED zone = trend day down, do not fade. \
A NEUTRAL IB with INTACT zone = two-sided day, trade the range.
4. SIGNALS LAST: VBH signals show WHERE price is relative to supply/demand zones, \
not WHY price is moving. A SHORT signal means price is at a supply zone, not that price is weak.

SIGNAL LEVEL MEANINGS (for ALL symbols — equities, ETFs, futures):
- entry = VBH zone price (support for LONG, resistance for SHORT) — this IS the key S/R level
- stop  = invalidation level (below red cloud for LONG, above green cloud for SHORT)
- t1    = 1:1 risk-reward target
- target = extended target (gray line — T2)
- h_high/h_low = current session high/low used to compute the levels
- [ENTRY] = price is at/beyond the zone right now  [NEAR] = approaching  [NEUTRAL] = mid-range
- daily_bias = opening gap direction (LONG = gapped up, SHORT = gapped down)

═══ MARKET PROFILE / DALTON FRAMEWORK ═══

CORE CONCEPT: Jim Dalton's Market Profile maps price against time (TPO letters). \
Each 30-minute RTH period = one letter (A=9:30, B=10:00, C=10:30 … M=3:30 PM ET). \
The Initial Balance (IB) = first 60 minutes (A+B periods, 9:30-10:30 AM). \
It sets the day's directional hypothesis — everything after tests or confirms it.

KEY LEVELS:
- ONH / ONL: Overnight High / Low (6 PM – 9:30 AM ET) — the range overnight traders defended
- ON POC: Overnight Point of Control — the price with the most overnight volume; acts as a pivot
- ON VAH / ON VAL: Overnight Value Area High/Low — 70% of overnight volume
- IB High / IB Low: Extremes of the first 60 minutes — the day's initial auction range
- Prior VAH / VAL: Previous session's value area — first extension targets

IB SCORE (−4 to +4) — assessed once at 10:30 AM, never changes:
- P1 Open vs Overnight Range: open above ONH = +1, open below ONL = −1, OA open (inside) = 0
- P2 IB vs ONH/ONL: accepted above ONH = +2, probe rejected = +1; accepted below ONL = −2, probe rejected = −1; IB absorbed entire ON range = 0
- P3 IB vs ON POC: IB entirely above = +1, IB entirely below = −1, straddles = 0
- P4 Overnight inventory: trended up + IB above ON POC = +1; trended down + IB below ON POC = −1
Score ≥+2 = BULLISH | +1 = BULLISH LEAN | 0 = NEUTRAL | −1 = BEARISH LEAN | ≤−2 = BEARISH | ±4 = maximum conviction

TWO-LAYER LIVE SCORE: IB score (fixed) + zone adjustment (updates each period) = current score.
Zone adjustments are direction-aware — for a bearish IB (negative score), direction = −1:
CONFIRMED=−2, INTACT=0, WEAKENING=+1, CRITICAL=+2, INVALIDATED=+3 (× direction).
Example: IB −4 + CONFIRMED adj −2 = −6 (Strong Bearish). IB +1 + INVALIDATED adj −3 = −2 (Bearish flip).

ZONE HIERARCHY (bearish IB — mirror for bullish):
- CONFIRMED: Closed below IB Low → sellers accelerating, trend day developing. Ride shorts, trail stop above ONL.
- INTACT: Closed below ONL → bearish excess holding, ONL is resistance. Sell rallies to ONL.
- WEAKENING: Closed between ONL and ON POC → signal fading, two-sided OA active. Cover shorts 50%, no new entries.
- CRITICAL: Closed at ON POC ± 3 ticks → last defence for bears. Cover all shorts.
- INVALIDATED: Closed above ON POC → bearish thesis broken. Reverse — buy dips to ON POC (now support).
Downgrades require TWO consecutive period closes. First dip = warning badge held. Upgrades are immediate.

OPENING TYPES:
- OA (Open Auction): Open inside overnight range → two-sided, buy ONL / sell ONH until one side breaks
- OD (Open Drive): Open outside prior VA, A continued in that direction → one-timeframe control, do not fade
- OTD (Open Test Drive): Open outside prior VA, A tested boundary then drove further → directional, high conviction
- ORR (Open Rejection Reverse): Open outside prior VA, A period reversed back inside → fading the gap, trade the reversal

DAY TYPE (classified after IB):
- Trend Day: Both sides extended + total range > 2.5× IB → do not fade, hold for limit moves
- Normal Variation: One-sided extension > 25% of IB → winners of IB auction in control
- Normal: Wide IB (>55% of prior range) → balanced, rotate within IB
- Neutral: Both sides extended but balanced → two-sided, no directional edge

ANSWERING MARKET PROFILE QUESTIONS:
- "What does IB −4 mean?" → Maximum bearish conviction: all four pillars (open, IB vs ONL, IB vs ON POC, inventory) aligned bearish.
- "What zone are we in?" → Reference the live Market Profile snapshot below.
- "Should I fade this move?" → Check zone: CONFIRMED = never fade. WEAKENING/INVALIDATED = fading is correct.
- "What is ON POC?" → The overnight price with the most volume. Acts as a magnet and pivot — bulls must defend it, bears must break it.

═══ END MARKET PROFILE ═══

FOR ANY STOCK OR ETF (BABA, AAPL, TSLA, etc.):
- Map it to its sector (BABA → China tech → KWEB/FXI context)
- Use live internals to judge broad market tone
- Give a direct view: is the setup long or short, what are the key levels, what is the risk
- Always reference the actual entry/stop/target numbers from the snapshot when discussing a symbol

ANSWERING "WHY is X weak/strong?":
- Look at its leading sectors first — are they down? That's why.
- Check $TICK and $TRIN — is the broader market selling?
- Check A/D ratio — is weakness broad or narrow?
- THEN mention the signal and Market Profile zone as confirmation, not as the cause.

STYLE: Conversational, 2-4 sentences. No bullet lists unless listing multiple items. \
No tables. Speak like a trading desk colleague, not a report. \
Reference actual numbers when available. \
Flag conflicts clearly (e.g. "internals are bullish but Market Profile zone is WEAKENING — wait for clarity").\
"""

def _build_ask_ai_context() -> str:
    """Snapshot of current signals, sectors, and prices for Gemini context."""
    lines: list[str] = ['=== LIVE DASHBOARD SNAPSHOT ===']

    # Sector ETFs with attribution to which futures they lead
    sec = _sector_pcts()
    if sec:
        lines.append('\nSECTOR ETFs today (% from RTH open):')
        SECTOR_LEADS = {
            'XLK': 'leads /NQ & /ES (Tech)',
            'XLC': 'leads /NQ (Comms)',
            'XLY': 'leads /NQ & /YM (Consumer Disc)',
            'XLV': 'leads /YM & /ES & /RTY (Healthcare)',
            'XLF': 'leads /YM & /ES & /RTY (Financials)',
            'XLI': 'leads /YM & /RTY (Industrials)',
            'XLP': 'defensive (Staples)',
            'XLE': 'energy sector',
            'XLB': 'materials sector',
            'XLU': 'defensive (Utilities)',
            'XLRE': 'rate-sensitive (Real Estate)',
            'SMH': 'semiconductors (NQ proxy)',
        }
        for ticker, pct in sec.items():
            arrow = '▲' if pct > 0 else ('▼' if pct < 0 else '—')
            label = SECTOR_LEADS.get(ticker, ticker)
            lines.append(f'  {ticker} {arrow}{abs(pct):.2f}%  [{label}]')

    sigs = state.get('signals', [])

    # ── ACTIVE signals (ENTRY / NEAR) — full detail ──────────────────────────
    active = [s for s in sigs if s.get('signal_state') in ('ENTRY', 'NEAR')]
    if active:
        lines.append(f'\nACTIVE AT ZONE ({len(active)} signals):')
        for s in active:
            sym  = s.get('symbol', '').split(':')[0]
            mo   = s.get('mo_state', '')
            mo_plain = {
                'POS_UP': 'momentum rising',
                'POS_DN': 'momentum fading ← best reversal setup',
                'NEG_UP': 'momentum recovering',
                'NEG_DN': 'momentum falling hard',
            }.get(mo, '')
            lines.append(
                f"  {sym} {s.get('side')} [{s.get('signal_state')}] model:{s.get('model')} "
                f"last:{s.get('last')}  entry:{s.get('entry')}  stop:{s.get('stop')}  "
                f"t1:{s.get('t1')}  target:{s.get('target')}"
                + (f'  ({mo_plain})' if mo_plain else '')
            )
    else:
        lines.append('\nNo signals currently at entry/near zones.')

    # ── ALL symbol levels (compact — one row per symbol, CON model preferred) ─
    # Groups signals by symbol so the AI can answer "what are KO's levels?"
    # regardless of whether it's currently at a zone or not.
    sym_best: dict[str, dict] = {}
    MODEL_PREF = {'CON': 0, 'AGG': 1, 'WIDE': 2, 'CR': 3}
    for s in sigs:
        sym = s.get('symbol', '').split(':')[0]
        model = s.get('model', '')
        existing = sym_best.get(sym)
        if not existing or MODEL_PREF.get(model, 9) < MODEL_PREF.get(existing.get('model', ''), 9):
            sym_best[sym] = s

    if sym_best:
        lines.append('\nALL SYMBOL LEVELS (entry=demand/supply zone, stop=L3 boundary, target=gray):')
        for sym, s in sorted(sym_best.items()):
            state_str = s.get('signal_state', 'NEUTRAL')
            bias_str  = f"  bias:{s.get('daily_bias')}" if s.get('daily_bias') else ''
            lines.append(
                f"  {sym} {s.get('side')} [{state_str}]{bias_str}  "
                f"last:{s.get('last')}  entry:{s.get('entry')}  "
                f"stop:{s.get('stop')}  t1:{s.get('t1')}  target:{s.get('target')}  "
                f"h_high:{s.get('hour_high')}  h_low:{s.get('hour_low')}"
            )

    if sigs:
        shorts = sum(1 for s in sigs if s.get('side') == 'SHORT')
        longs  = sum(1 for s in sigs if s.get('side') == 'LONG')
        lines.append(f'\nTotal signals: {len(sigs)} ({longs} long, {shorts} short)')

    return '\n'.join(lines)


def _build_market_profile_context() -> str:
    """Compact Market Profile snapshot for Ask AI — reads from state cache.

    The cache is populated by /api/market-profile/{symbol} calls which the
    frontend makes every 60 seconds. No extra Schwab calls needed here.
    """
    MP_SYMBOLS = ['/ES', '/NQ', '/YM', '/RTY']
    cache = state.get('market_profile', {})

    lines = ['\n=== MARKET PROFILE SNAPSHOT (from last frontend poll) ===']
    found = False
    for sym in MP_SYMBOLS:
        data = cache.get(sym)
        if not data:
            continue
        found = True

        ib      = data.get('ib_signals', {})
        lr      = data.get('live_read', {})
        on      = data.get('overnight', {})
        today   = data.get('today', {})

        bias        = ib.get('bias_label', 'Neutral')
        ib_score    = ib.get('ib_score', 0)
        zone        = lr.get('status', 'BUILDING')
        curr_label  = lr.get('current_label', '')
        curr_score  = lr.get('current_score', ib_score)
        live_adj    = lr.get('live_adjustment', 0)
        last_period = lr.get('last_period') or '—'
        last_close  = lr.get('last_close')
        trade_plan  = lr.get('live_trade_plan') or ib.get('trade_plan', '')
        on_high     = on.get('high')
        on_low      = on.get('low')
        on_poc      = on.get('poc')
        ib_high     = today.get('ib_high')
        ib_low      = today.get('ib_low')
        computed_at = data.get('computed_at', '')

        close_str = f'{last_close:.2f}' if last_close else '—'
        lines.append(
            f'\n{sym} (as of {computed_at})'
            f'\n  IB:{ib_score:+d} ({bias}) + adj {live_adj:+d} = {curr_score:+d} ({curr_label})'
            f'  Zone:{zone}  Last period:{last_period} @ {close_str}'
        )
        if on_high and on_low and on_poc:
            lines.append(
                f'  Key levels — ONH:{on_high}  ONL:{on_low}  ON POC:{on_poc}'
                + (f'  IB High:{ib_high}  IB Low:{ib_low}' if ib_high and ib_low else '')
            )
        if trade_plan:
            lines.append(f'  Live trade plan: {trade_plan}')

    if not found:
        lines.append(
            '  No Market Profile data cached yet — user has not opened the Market Profile tab '
            'or market is pre-RTH. Explain concepts from knowledge above instead.'
        )
    return '\n'.join(lines)


@app.post('/api/ai/ask')
async def ask_ai(body: dict = Body(...)):
    """Gemini-powered conversational assistant with live dashboard context."""
    message = body.get('message', '').strip()
    history = body.get('history', [])   # [{role: 'user'|'model', content: str}]

    if not message:
        return {'reply': ''}

    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return {'reply': 'GEMINI_API_KEY not configured.'}

    # Gather internals + signals context concurrently; MP context reads from cache (no I/O)
    internals, ctx = await asyncio.gather(
        _internals_snapshot(),
        asyncio.to_thread(_build_ask_ai_context),
    )
    mp_ctx = _build_market_profile_context()

    # Append internals and market profile to context
    ctx_lines = [ctx, mp_ctx]
    if internals and any(internals.values()):
        tick = internals.get('tick', 0)
        trin = internals.get('trin', 0)
        advn = internals.get('advn', 0)
        decn = internals.get('decn', 0)
        vix  = internals.get('vix', 0)
        vxn  = internals.get('vxn', 0)

        tick_read = ('strongly bullish — avoid new shorts' if tick > 800 else
                     'strongly bearish — avoid new longs'  if tick < -800 else
                     'mildly bullish' if tick > 200 else
                     'mildly bearish' if tick < -200 else 'neutral')
        trin_read = ('buying volume dominant' if trin < 0.8 else
                     'selling volume dominant' if trin > 1.5 else 'neutral volume')
        ad_ratio  = f'{advn/decn:.1f}:1 advancing' if decn > 0 else 'n/a'

        ctx_lines.append(
            f'\nMARKET INTERNALS (NYSE):\n'
            f'  $TICK: {tick:+.0f} → {tick_read}\n'
            f'  $TRIN: {trin:.2f} → {trin_read}\n'
            f'  A/D ratio: {ad_ratio}  ({advn:,} advancing / {decn:,} declining)\n'
            f'  VIX: {vix:.1f}   NASDAQ VIX: {vxn:.1f}'
        )
    else:
        ctx_lines.append('\nMARKET INTERNALS: not available (market closed or pre-RTH)')
    context_block = '\n'.join(ctx_lines)

    try:
        from google import genai as _genai
        from google.genai import types as _gtypes

        client = _genai.Client(api_key=api_key)

        # Build conversation history for Gemini
        gem_history = []
        for msg in history:
            role    = 'user' if msg.get('role') == 'user' else 'model'
            content = msg.get('content', '')
            gem_history.append(_gtypes.Content(
                role=role,
                parts=[_gtypes.Part(text=content)],
            ))

        # Inject live context into the user message
        user_content = f'{context_block}\n\nTrader question: {message}'

        resp = client.models.generate_content(
            model    = 'gemini-2.5-flash',
            contents = gem_history + [
                _gtypes.Content(role='user', parts=[_gtypes.Part(text=user_content)])
            ],
            config   = _gtypes.GenerateContentConfig(
                system_instruction = _ASK_AI_SYSTEM,
                max_output_tokens  = 600,
                temperature        = 0.4,
                thinking_config    = _gtypes.ThinkingConfig(thinking_budget=0),  # disable thinking → fast
            ),
        )
        reply = (resp.text or '').strip()
        if not reply:
            return {'reply': 'No response from AI — please try again.'}
        return {'reply': reply}

    except Exception as exc:
        log.warning('ask_ai Gemini error: %s', exc)
        return {'reply': f'Error: {exc}'}


# ── On-demand Signal Advisory ─────────────────────────────────────────────────

try:
    import talib as _talib
    _HAS_TALIB = True
    log.info('TA-Lib loaded — using C-backed MACD')
except ImportError:
    _HAS_TALIB = False
    log.info('TA-Lib not available — falling back to pandas EWM for MACD')


# Candlestick patterns to scan — (talib_func_name, readable_name, direction)
# direction: 'bear'=bearish reversal, 'bull'=bullish reversal, 'both'=either
_CANDLE_PATTERNS: list[tuple[str, str, str]] = [
    ('CDLSHOOTINGSTAR',   'Shooting Star',    'bear'),
    ('CDLHANGINGMAN',     'Hanging Man',      'bear'),
    ('CDLEVENINGSTAR',    'Evening Star',     'bear'),
    ('CDLDARKCLOUDCOVER', 'Dark Cloud Cover', 'bear'),
    ('CDLHAMMER',         'Hammer',           'bull'),
    ('CDLINVERTEDHAMMER', 'Inverted Hammer',  'bull'),
    ('CDLMORNINGSTAR',    'Morning Star',     'bull'),
    ('CDLPIERCING',       'Piercing Line',    'bull'),
    ('CDLDRAGONFLYDOJI',  'Dragonfly Doji',   'bull'),
    ('CDLENGULFING',      'Engulfing',        'both'),  # +100=bull, -100=bear
    ('CDLDOJI',           'Doji',             'both'),
    ('CDLHARAMI',         'Harami',           'both'),
]


def _technical_context(candles: list[dict],
                       fast: int = 8, slow: int = 17, sig_period: int = 9,
                       rsi_period: int = 14, atr_period: int = 14) -> dict:
    """
    Compute MACD(8,17,9), RSI(14), ATR(14), and candlestick patterns on 5-min bars.
    Uses TA-Lib when available; falls back to pandas EWM (identical math, adjust=False).
    Returns a plain-English dict for the AI advisory prompt.
    """
    min_bars = max(slow + sig_period, rsi_period, atr_period) + 10
    if not candles or len(candles) < min_bars:
        return {}
    try:
        closes = np.array([float(c['close']) for c in candles])
        highs  = np.array([float(c['high'])  for c in candles])
        lows   = np.array([float(c['low'])   for c in candles])
        opens  = np.array([float(c['open'])  for c in candles])

        result: dict = {}

        # ── MACD ──────────────────────────────────────────────────────────────
        if _HAS_TALIB:
            macd_arr, sig_arr, hist_arr = _talib.MACD(
                closes, fastperiod=fast, slowperiod=slow, signalperiod=sig_period
            )
            valid = ~np.isnan(hist_arr)
            if valid.sum() >= 2:
                h  = hist_arr[valid]; m  = macd_arr[valid]; s = sig_arr[valid]
                h_now, h_prev = h[-1], h[-2]
                m_now, m_prev = m[-1], m[-2]
                s_now, s_prev = s[-1], s[-2]
            else:
                h_now = h_prev = m_now = m_prev = s_now = s_prev = None
        else:
            cs = pd.Series(closes)
            macd_line   = cs.ewm(span=fast, adjust=False).mean() - cs.ewm(span=slow, adjust=False).mean()
            signal_line = macd_line.ewm(span=sig_period, adjust=False).mean()
            histogram   = macd_line - signal_line
            h_now,  h_prev  = histogram.iloc[-1],   histogram.iloc[-2]
            m_now,  m_prev  = macd_line.iloc[-1],   macd_line.iloc[-2]
            s_now,  s_prev  = signal_line.iloc[-1], signal_line.iloc[-2]

        if h_now is not None:
            crossed_bull = m_prev < s_prev and m_now > s_now
            crossed_bear = m_prev > s_prev and m_now < s_now
            if crossed_bull:
                hist_state = 'fresh bullish crossover — MACD just crossed above signal line'
            elif crossed_bear:
                hist_state = 'fresh bearish crossover — MACD just crossed below signal line'
            elif h_now > 0 and h_now < h_prev:
                hist_state = 'histogram positive but shrinking — upward momentum fading (prime reversal-short setup)'
            elif h_now > 0:
                hist_state = 'histogram positive and still growing — upward momentum intact, wait before shorting'
            elif h_now < 0 and abs(h_now) < abs(h_prev):
                hist_state = 'histogram negative but shrinking — downward momentum fading (prime reversal-long setup)'
            elif h_now < 0:
                hist_state = 'histogram negative and growing — downward momentum accelerating, avoid longs'
            else:
                hist_state = 'histogram near zero — momentum directionless'
            result['macd'] = {
                'hist_state': hist_state,
                'zero_pos'  : 'MACD line above zero (broader uptrend)' if m_now > 0 else 'MACD line below zero (broader downtrend)',
            }

        # ── RSI ───────────────────────────────────────────────────────────────
        if _HAS_TALIB:
            rsi_arr = _talib.RSI(closes, timeperiod=rsi_period)
            rsi_val = float(rsi_arr[~np.isnan(rsi_arr)][-1]) if (~np.isnan(rsi_arr)).any() else None
        else:
            delta    = pd.Series(closes).diff()
            avg_gain = delta.clip(lower=0).ewm(alpha=1/rsi_period, adjust=False).mean()
            avg_loss = (-delta.clip(upper=0)).ewm(alpha=1/rsi_period, adjust=False).mean()
            rs       = avg_gain / avg_loss.replace(0, np.nan)
            rsi_val  = float((100 - 100 / (1 + rs)).iloc[-1])

        if rsi_val is not None and not np.isnan(rsi_val):
            if rsi_val >= 80:
                rsi_label = f'{rsi_val:.1f} — strongly overbought, high reversal-short probability'
            elif rsi_val >= 70:
                rsi_label = f'{rsi_val:.1f} — overbought, supports reversal short'
            elif rsi_val <= 20:
                rsi_label = f'{rsi_val:.1f} — strongly oversold, high reversal-long probability'
            elif rsi_val <= 30:
                rsi_label = f'{rsi_val:.1f} — oversold, supports reversal long'
            elif 45 <= rsi_val <= 55:
                rsi_label = f'{rsi_val:.1f} — neutral, no overbought/oversold edge'
            else:
                rsi_label = f'{rsi_val:.1f} — neutral range'
            result['rsi'] = rsi_label

        # ── ATR ───────────────────────────────────────────────────────────────
        if _HAS_TALIB:
            atr_arr = _talib.ATR(highs, lows, closes, timeperiod=atr_period)
            atr_val = float(atr_arr[~np.isnan(atr_arr)][-1]) if (~np.isnan(atr_arr)).any() else None
        else:
            hs = pd.Series(highs); ls = pd.Series(lows); cs2 = pd.Series(closes)
            tr = pd.concat([hs - ls, (hs - cs2.shift()).abs(), (ls - cs2.shift()).abs()], axis=1).max(axis=1)
            atr_val = float(tr.ewm(alpha=1/atr_period, adjust=False).mean().iloc[-1])

        if atr_val is not None and not np.isnan(atr_val):
            result['atr'] = round(atr_val, 2)

        # ── Candlestick patterns ───────────────────────────────────────────────
        fired: list[str] = []
        if _HAS_TALIB:
            for func_name, label, direction in _CANDLE_PATTERNS:
                fn  = getattr(_talib, func_name, None)
                if fn is None:
                    continue
                arr = fn(opens, highs, lows, closes)
                val = int(arr[-1])
                if val != 0:
                    qualifier = ''
                    if direction == 'both':
                        qualifier = ' (bullish)' if val > 0 else ' (bearish)'
                    fired.append(f'{label}{qualifier}')
        else:
            # Pure numpy/pandas fallback — key reversal patterns only
            o, h, l, c = opens[-3:], highs[-3:], lows[-3:], closes[-3:]
            body   = abs(c - o)
            rng    = h - l
            rng    = np.where(rng == 0, 1e-9, rng)   # avoid div/0

            # Last bar (index -1)
            upper_shadow = h[-1] - max(o[-1], c[-1])
            lower_shadow = min(o[-1], c[-1]) - l[-1]
            body_last    = body[-1]
            rng_last     = rng[-1]
            bull_bar     = c[-1] > o[-1]

            # Doji: body < 10% of range
            if body_last < 0.10 * rng_last:
                fired.append('Doji')

            # Shooting Star (bearish): upper shadow ≥ 2× body, lower shadow ≤ 15% range, bearish close
            elif (upper_shadow >= 2 * body_last
                  and lower_shadow <= 0.15 * rng_last
                  and not bull_bar):
                fired.append('Shooting Star')

            # Hammer (bullish): lower shadow ≥ 2× body, upper shadow ≤ 15% range, bullish close
            elif (lower_shadow >= 2 * body_last
                  and upper_shadow <= 0.15 * rng_last
                  and bull_bar):
                fired.append('Hammer')

            # Engulfing (need 2 bars): current body fully contains previous body
            if len(c) >= 2:
                prev_bull = c[-2] > o[-2]
                curr_bull = c[-1] > o[-1]
                if (not curr_bull and prev_bull              # bearish engulfing
                        and o[-1] >= c[-2] and c[-1] <= o[-2]):
                    fired.append('Engulfing (bearish)')
                elif (curr_bull and not prev_bull            # bullish engulfing
                        and o[-1] <= c[-2] and c[-1] >= o[-2]):
                    fired.append('Engulfing (bullish)')

        result['patterns'] = fired

        return result
    except Exception as exc:
        log.debug('_technical_context error: %s', exc)
        return {}


_SIGNAL_ADVISORY_SYSTEM = (
    'You are a senior futures trader giving a quick pre-trade check. '
    'You receive full context: price vs key levels, momentum, volatility setup, market internals, and sectors. '
    'SYNTHESIZE ALL of it — never focus on a single indicator. '
    'Your reason must mention AT LEAST: (1) where price is relative to a key level, '
    '(2) what momentum/internals say, (3) the verdict rationale. '
    'Use plain English only. NEVER use these words: squeeze, MACD, VAH, VAL, POC, VWAP, IB, ATR, '
    'POS_UP, POS_DN, NEG_UP, NEG_DN, sq_confirm, mo_state, VBH, CON, AGG. '
    'Respond ONLY with valid JSON (no markdown): '
    '{"verdict": "ENTER" | "WAIT" | "SKIP", "reason": "2-3 plain sentences, max 40 words"}'
)


@app.post('/api/ai/signal-advisory')
async def signal_advisory(body: dict = Body(...)):
    """Return a plain-English ENTER / WAIT / SKIP verdict for a single signal row."""
    symbol = body.get('symbol', '').split(':')[0]
    model  = body.get('model', '')
    side   = body.get('side', '')

    # Find the matching signal in live state
    sig = next(
        (s for s in state.get('signals', [])
         if s.get('symbol', '').split(':')[0] == symbol
         and s.get('model') == model
         and s.get('side') == side),
        None,
    )

    # Gather internals, sector pcts, MP levels, and 5-min candles concurrently
    internals, sec_pcts, agent_data, candles_5m = await asyncio.gather(
        _internals_snapshot(),
        asyncio.to_thread(_sector_pcts),
        _fetch_agent_symbol_data(symbol),
        asyncio.to_thread(get_candles, symbol, 3, 5),   # 3 days of 5-min bars for MACD
    )
    ta = _technical_context(candles_5m)

    # --- Build the plain-English context prompt ---
    lines: list[str] = [
        f'Instrument: {symbol}  Direction: {side}  Model: {model}',
    ]

    # Market Profile levels (from agent data)
    if agent_data:
        lv    = agent_data.get('levels', {})
        price = agent_data.get('price', 0)

        # Last session close — key battle level
        prev_close = lv.get('prev_close') or lv.get('prev_rth_close')
        if prev_close and price:
            rel = 'above' if price > prev_close else 'below'
            lines.append(
                f'Price vs last session close ({prev_close}): currently {rel} — '
                f'{"bulls in control above this level" if rel == "above" else "bears in control below this level"}'
            )

        # Prior RTH TPO Value Area (Dalton — most accurate)
        vah  = lv.get('prior_rth_tpo_vah')  or lv.get('prior_rth_vah')
        val  = lv.get('prior_rth_tpo_val')  or lv.get('prior_rth_val')
        vpoc = lv.get('prior_rth_tpo_vpoc') or lv.get('prior_rth_vpoc')
        if vah and val and price:
            if price > vah:
                va_pos = f'Price is ABOVE yesterday\'s value area ({val}–{vah}) — extended above prior accepted range, reversal risk'
            elif price < val:
                va_pos = f'Price is BELOW yesterday\'s value area ({val}–{vah}) — extended below prior accepted range, reversal risk'
            else:
                va_pos = f'Price is INSIDE yesterday\'s value area ({val}–{vah}) — within prior accepted range, two-sided'
            if vpoc:
                va_pos += f'  |  Yesterday\'s most-traded price (fair value): {vpoc}'
            lines.append(va_pos)

        # Overnight TPO Value Area
        on_vah  = lv.get('overnight_tpo_vah')  or lv.get('overnight_vah')
        on_val  = lv.get('overnight_tpo_val')   or lv.get('overnight_val')
        on_vpoc = lv.get('overnight_tpo_vpoc')  or lv.get('overnight_vpoc')
        if on_vah and on_val and price:
            if price > on_vah:
                lines.append(f'Price is above overnight range ({on_val}–{on_vah}) — extended above overnight acceptance')
            elif price < on_val:
                lines.append(f'Price is below overnight range ({on_val}–{on_vah}) — extended below overnight acceptance')
            else:
                lines.append(f'Price is inside overnight range ({on_val}–{on_vah}), overnight fair value: {on_vpoc}')

        # Developing (today's) value area
        dev_vah  = lv.get('developing_tpo_vah')  or lv.get('developing_vah')
        dev_val  = lv.get('developing_tpo_val')   or lv.get('developing_val')
        dev_vpoc = lv.get('developing_tpo_vpoc')  or lv.get('developing_vpoc')
        if dev_vah and dev_val and price:
            lines.append(f'Today\'s developing value area: {dev_val}–{dev_vah}, fair value building at {dev_vpoc}')

        # Key reference levels
        pivot = lv.get('daily_pivot')
        if pivot and price:
            lines.append(f'Daily pivot (H+L+C/3 yesterday): {pivot} — price {"above" if price > pivot else "below"} pivot')
        mcvpoc = lv.get('mcvpoc_3day')
        if mcvpoc and price:
            lines.append(f'3-day composite fair value: {mcvpoc} — price {"above" if price > mcvpoc else "below"} multi-day fair value')
        vwap = lv.get('vwap')
        if vwap and price:
            lines.append(f'VWAP (today\'s volume-weighted average): {vwap} — price {"above" if price > vwap else "below"}')
        prev_hi = lv.get('prev_high'); prev_lo = lv.get('prev_low')
        if prev_hi and prev_lo:
            lines.append(f'Yesterday\'s range: {prev_lo}–{prev_hi}')

        # Gap
        gap = lv.get('gap')
        if gap is not None and abs(gap) >= 0.25:
            prev_rth = lv.get('prev_close') or prev_close
            direction = 'opened higher' if gap > 0 else 'opened lower'
            filled = (price <= prev_rth) if gap > 0 else (price >= prev_rth)
            lines.append(
                f'Today {direction} by {abs(gap):.1f} pts from yesterday close ({prev_rth}) — '
                f'gap is {"already filled" if filled else f"still open, gap-fill target {prev_rth}"}'
            )

        # Initial Balance
        ib_high = lv.get('ib_high')
        ib_low  = lv.get('ib_low')
        if ib_high and ib_low:
            ib_range = round(ib_high - ib_low, 2)
            if price > ib_high:
                ib_pos = f'Price broke above opening range high ({ib_high}), range was {ib_range} pts — bullish extension'
            elif price < ib_low:
                ib_pos = f'Price broke below opening range low ({ib_low}), range was {ib_range} pts — bearish extension'
            else:
                ib_pos = f'Price inside opening range ({ib_low}–{ib_high}, {ib_range} pts) — no breakout yet'
            lines.append(ib_pos)

    if sig:
        state_label = sig.get('signal_state', 'UNKNOWN')
        lines.append(f'Signal state: {state_label}  (ENTRY = price is at the zone right now; NEAR = approaching)')
        entry = sig.get('entry', 0)
        stop  = sig.get('stop',  0)
        l1    = sig.get('l1',    0)
        lines.append(f'Entry zone: {entry}   Stop loss: {stop}   First target: {l1}')

        mo = sig.get('mo_state', '')
        mo_plain = {
            'POS_UP': 'momentum is rising — price still pushing in trend direction',
            'POS_DN': 'momentum is decelerating — trend is fading, good reversal setup',
            'NEG_UP': 'momentum was negative but recovering — trend may be reversing back',
            'NEG_DN': 'momentum strongly negative — price falling fast',
        }.get(mo, 'momentum data unavailable')
        lines.append(f'Momentum: {mo_plain}')

        sq = sig.get('sq_confirm', '')
        sq_plain = {
            'CONFIRMED': 'volatility compression confirmed — breakout energy building',
            'CAUTION':   'mild volatility compression — uncertain setup',
            'NEGATED':   'no volatility compression — setup is weaker',
            'NEUTRAL':   'squeeze neutral — no strong signal from volatility',
        }.get(sq, 'squeeze data unavailable')
        lines.append(f'Volatility setup: {sq_plain}')

        sq_reason = sig.get('sq_reason', '')
        if sq_reason:
            lines.append(f'Detail: {sq_reason}')

    # ── Technical indicators (5-min bars) ────────────────────────────────────
    if ta:
        macd = ta.get('macd', {})
        if macd:
            lines.append(f'MACD 5-min (8/17/9): {macd["hist_state"]}  |  {macd["zero_pos"]}')

        rsi = ta.get('rsi')
        if rsi:
            lines.append(f'RSI(14): {rsi}')

        atr = ta.get('atr')
        if atr and sig:
            entry_p = sig.get('entry', 0)
            stop_p  = sig.get('stop',  0)
            if entry_p and stop_p:
                stop_dist = abs(entry_p - stop_p)
                ratio     = stop_dist / atr if atr else 0
                if ratio < 0.5:
                    atr_label = f'{atr} pts — stop is very tight ({ratio:.1f}× ATR), high risk of getting stopped out prematurely'
                elif ratio <= 1.5:
                    atr_label = f'{atr} pts — stop distance is {ratio:.1f}× ATR (healthy range)'
                else:
                    atr_label = f'{atr} pts — stop is wide ({ratio:.1f}× ATR), risk-reward may be poor'
                lines.append(f'ATR(14): {atr_label}')
            else:
                lines.append(f'ATR(14): {atr} pts per 5-min bar (current volatility)')

        patterns = ta.get('patterns', [])
        if patterns:
            lines.append(f'Last candle pattern(s): {", ".join(patterns)} — reversal signal at this level')
        else:
            lines.append('Last candle pattern: none detected on most recent 5-min bar')

    # Sector alignment
    sym_sectors = SECTORS_FOR.get(symbol, [])
    sec_parts   = []
    for ticker, name in sym_sectors:
        pct   = sec_pcts.get(ticker, 0.0)
        arrow = '▲' if pct > 0 else ('▼' if pct < 0 else '—')
        sec_parts.append(f'{name}({ticker}) {arrow}{abs(pct):.2f}%')
    if sec_parts:
        lines.append('Related sectors today: ' + '  '.join(sec_parts))

    # Market internals
    if internals:
        tick  = internals.get('tick', 0)
        trin  = internals.get('trin', 0)
        advn  = internals.get('advn', 0)
        decn  = internals.get('decn', 0)
        vix   = internals.get('vix', 0)
        vxn   = internals.get('vxn', 0)

        tick_plain = (
            'NYSE tick strongly bullish — avoid new shorts' if tick > 800 else
            'NYSE tick strongly bearish — avoid new longs' if tick < -800 else
            'NYSE tick mildly bullish' if tick > 200 else
            'NYSE tick mildly bearish' if tick < -200 else
            'NYSE tick neutral'
        )
        trin_plain = (
            'buying volume dominant (bull money flow)' if trin < 0.8 else
            'selling volume dominant (bear money flow)' if trin > 1.5 else
            'volume flow neutral'
        )
        ad_plain = (
            f'{advn:,} stocks rising vs {decn:,} falling'
            if advn or decn else 'breadth data unavailable'
        )
        lines += [
            f'Market breadth: {tick_plain}  |  {trin_plain}',
            f'Advancing/declining: {ad_plain}',
            f'Volatility index: VIX {vix:.1f}  NASDAQ VIX {vxn:.1f}',
        ]

    prompt = '\n'.join(lines)
    log.debug('signal_advisory prompt:\n%s', prompt)

    try:
        import anthropic
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {'verdict': 'WAIT', 'reason': 'AI not configured.'}

        client = anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model      = 'claude-haiku-4-5',
            max_tokens = 200,
            system     = _SIGNAL_ADVISORY_SYSTEM,
            messages   = [{'role': 'user', 'content': prompt}],
        )
        import json as _json
        raw = resp.content[0].text.strip()
        # Strip markdown fences if model adds them
        raw = raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        data = _json.loads(raw)
        verdict = data.get('verdict', 'WAIT').upper()
        if verdict not in ('ENTER', 'WAIT', 'SKIP'):
            verdict = 'WAIT'
        return {'verdict': verdict, 'reason': data.get('reason', '—')}
    except Exception as exc:
        log.warning('signal_advisory error: %s', exc)
        return {'verdict': 'WAIT', 'reason': 'Could not fetch advisory right now.'}


# ── Economic Calendar (Briefing) ───────────────────────────────────────────────

_briefing_cache: dict = {'data': None, 'fetched_at': 0.0}
_BRIEFING_TTL = 3600        # refresh once per hour
_FF_BASE = 'https://nfs.faireconomy.media'
# Only show USD events + High-impact events from other major currencies
_MAJOR = {'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'NZD', 'CHF'}


def _fetch_ff(slug: str) -> list:
    try:
        r = _req.get(f'{_FF_BASE}/{slug}', timeout=10)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return []


def _build_briefing() -> dict:
    events: list = []
    for slug in ('ff_calendar_thisweek.json', 'ff_calendar_nextweek.json'):
        events.extend(_fetch_ff(slug))

    filtered = []
    for ev in events:
        country = ev.get('country', '')
        impact  = ev.get('impact', 'Low')
        if impact == 'Holiday':
            continue
        # Always include all USD events; include High-impact from other majors
        if country == 'USD' or (country in _MAJOR and impact == 'High'):
            filtered.append({
                'title'   : ev.get('title', ''),
                'country' : country,
                'date'    : ev.get('date', ''),
                'impact'  : impact,
                'forecast': ev.get('forecast', ''),
                'previous': ev.get('previous', ''),
            })

    # Sort by date ascending
    filtered.sort(key=lambda e: e['date'])
    return {'events': filtered, 'source': 'ForexFactory'}


@app.get('/api/briefing')
def get_briefing():
    now = time.time()
    if _briefing_cache['data'] and (now - _briefing_cache['fetched_at']) < _BRIEFING_TTL:
        return _briefing_cache['data']
    result = _build_briefing()
    if result['events']:          # only cache if we got real data
        _briefing_cache['data']       = result
        _briefing_cache['fetched_at'] = now
    elif _briefing_cache['data']:  # stale cache beats empty response
        return _briefing_cache['data']
    return result


@app.get('/api/levels-on/{symbol:path}')
async def get_levels_on(symbol: str, date: str = Query(..., description='YYYY-MM-DD')):
    """Retroactively compute all key levels as of the morning open on a specific date.
    Uses only 1-min bars available before that date's RTH session.
    Also returns which prior-session VPOCs were naked as of that morning.

    Fixes vs naive approach:
    - Sunday evening bars assigned to Monday overnight (CME reopens Sun 6 PM ET)
    - Weekly pivot uses extended-hours H/L from 1-min data (not RTH-only daily candles)
    - Weekly open uses target date's own opening bar
    - Swing high/low uses fractal detection (not simple 10-day min/max)
    """
    from datetime import date as date_cls
    symbol  = symbol.upper()
    tick    = _tick_for(symbol)

    try:
        target_date = date_cls.fromisoformat(date)
    except ValueError:
        return {'error': f'Invalid date format: {date}. Use YYYY-MM-DD.'}

    # Fetch max history concurrently
    raw_1min, daily = await asyncio.gather(
        asyncio.to_thread(get_candles, symbol, 30, 1),
        asyncio.to_thread(get_daily_candles, symbol, 30),
    )

    def _bar_date(c):
        return datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET).date()

    # ── Classify 1-min bars ─────────────────────────────────────────────────────
    # FIX: Sunday bars (CME overnight) → assigned to Monday's overnight session
    rth_by_date: dict = {}
    on_by_date:  dict = {}

    for c in raw_1min:
        dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        d     = dt.date()
        t_min = dt.hour * 60 + dt.minute
        wday  = dt.weekday()   # 0=Mon … 6=Sun

        is_rth       = wday < 5 and (9 * 60 + 30) <= t_min < 16 * 60
        is_on_wkday  = wday < 5 and t_min < (9 * 60 + 30)
        is_sunday    = wday == 6   # CME Sunday evening session

        if is_rth:
            rth_by_date.setdefault(d, []).append(c)
        elif is_on_wkday:
            on_by_date.setdefault(d, []).append(c)
        elif is_sunday:
            monday = d + timedelta(days=1)
            on_by_date.setdefault(monday, []).append(c)

    # Sessions strictly before target date
    prior_rth_dates = sorted([d for d in rth_by_date if d < target_date], reverse=True)

    # ── Session VPOC — most recent completed RTH before target ─────────────────
    session_vpoc = None
    for d in prior_rth_dates:
        v = _compute_vpoc(rth_by_date[d], tick)
        if v:
            session_vpoc = v
            break

    # ── Overnight VPOC — extended bars on target date morning (incl Sunday for Monday) ──
    overnight_vpoc = _compute_vpoc(on_by_date.get(target_date, []), tick)

    # ── MCVPOC 3-day — composite of 3 prior RTH sessions ──────────────────────
    mc3: list[dict] = []
    for d in prior_rth_dates[:3]:
        mc3.extend(rth_by_date[d])
    mcvpoc_3day = _compute_vpoc(mc3, tick) if mc3 else None

    # ── Daily candle derived levels ─────────────────────────────────────────────
    daily_pivot = weekly_pivot = weekly_open = None
    ath_intraday = swing_high = swing_low = None
    prev_high = prev_low = prev_close = None

    prior_daily = [c for c in daily if _bar_date(c) < target_date]

    if prior_daily:
        prev         = prior_daily[-1]
        prev_high    = prev['high']
        prev_low     = prev['low']
        prev_close   = prev['close']
        daily_pivot  = round((prev['high'] + prev['low'] + prev['close']) / 3, 4)
        ath_intraday = max(c['high'] for c in prior_daily)

        monday_this = target_date - timedelta(days=target_date.weekday())
        monday_prev = monday_this - timedelta(days=7)
        friday_prev = monday_prev + timedelta(days=4)

        # FIX: weekly open — include target date's own daily bar (handles Monday case where
        # prior_daily has no bars from this week yet but the daily list has target_date)
        this_week_daily = [c for c in daily if monday_this <= _bar_date(c) <= target_date]
        if this_week_daily:
            weekly_open = this_week_daily[0]['open']

        # FIX: swing high/low — compute from 1-min RTH session data, not daily candles.
        # Daily candles from get_daily_candles include extended-hours H/L even when
        # needExtendedHoursData=false for futures, which breaks the fractal pattern.
        sorted_prior_rth = sorted([d for d in rth_by_date if d < target_date])
        rth_sess_ohlc = []
        for sd in sorted_prior_rth:
            sb = rth_by_date[sd]
            rth_sess_ohlc.append({
                'high' : max(b['high'] for b in sb),
                'low'  : min(b['low']  for b in sb),
            })
        recent_sess = rth_sess_ohlc[-15:] if len(rth_sess_ohlc) >= 3 else rth_sess_ohlc
        _sh = _sl = None
        for i in range(len(recent_sess) - 2, 0, -1):
            if _sh is None and recent_sess[i]['high'] > recent_sess[i-1]['high'] and recent_sess[i]['high'] > recent_sess[i+1]['high']:
                _sh = recent_sess[i]['high']
            if _sl is None and recent_sess[i]['low'] < recent_sess[i-1]['low'] and recent_sess[i]['low'] < recent_sess[i+1]['low']:
                _sl = recent_sess[i]['low']
            if _sh is not None and _sl is not None:
                break
        swing_high = _sh or (max(s['high'] for s in recent_sess) if recent_sess else None)
        swing_low  = _sl or (min(s['low']  for s in recent_sess) if recent_sess else None)

        # FIX: weekly pivot — use 1-min extended-hours data for true H/L of prior week
        # RTH-only daily candles miss overnight highs (e.g. May 14 overnight hit 7540,
        # daily RTH candle showed 7525.5 — pivot is off by ~5 pts without this fix)
        prev_week_1min = [
            c for c in raw_1min
            if monday_prev <= _bar_date(c) <= friday_prev
        ]
        if prev_week_1min:
            wh = max(c['high'] for c in prev_week_1min)
            wl = min(c['low']  for c in prev_week_1min)
            prev_week_rth_dates = sorted([d for d in rth_by_date if monday_prev <= d <= friday_prev])
            if prev_week_rth_dates:
                wc = rth_by_date[prev_week_rth_dates[-1]][-1]['close']
                weekly_pivot = round((wh + wl + wc) / 3, 4)

    # ── Naked VPOCs as of target date morning ──────────────────────────────────
    all_prior_dates_sorted = sorted([d for d in rth_by_date if d < target_date])
    session_vpocs_list: list[dict] = []
    for d in all_prior_dates_sorted:
        v = _compute_vpoc(rth_by_date[d], tick)
        if v:
            session_vpocs_list.append({'date': d, 'vpoc': v})

    naked_vpocs = []
    for i, sv in enumerate(session_vpocs_list):
        vp = sv['vpoc']
        touched = False
        for j in range(i + 1, len(session_vpocs_list)):
            d2 = session_vpocs_list[j]['date']
            for b in rth_by_date.get(d2, []):
                if b['low'] <= vp <= b['high']:
                    touched = True
                    break
            if touched:
                break
        if not touched:
            naked_vpocs.append({'date': sv['date'].isoformat(), 'vpoc': sv['vpoc']})

    return {
        'symbol'     : symbol,
        'date'       : target_date.isoformat(),
        'tick'       : tick,
        'levels': {
            'session_vpoc'  : session_vpoc,
            'overnight_vpoc': overnight_vpoc,
            'mcvpoc_3day'   : mcvpoc_3day,
            'daily_pivot'   : daily_pivot,
            'weekly_pivot'  : weekly_pivot,
            'weekly_open'   : weekly_open,
            'ath_intraday'  : ath_intraday,
            'swing_high'    : swing_high,
            'swing_low'     : swing_low,
            'prev_high'     : prev_high,
            'prev_low'      : prev_low,
            'prev_close'    : prev_close,
        },
        'naked_vpocs': naked_vpocs,
    }


@app.get('/api/debug-overnight/{symbol:path}')
async def debug_overnight(symbol: str, date: str = Query(...)):
    """Debug: show what bars exist in on_by_date for a given date (including Sunday assignment)."""
    from datetime import date as date_cls
    symbol = symbol.upper()
    target_date = date_cls.fromisoformat(date)
    raw_1min = await asyncio.to_thread(get_candles, symbol, 30, 1)

    on_by_date: dict = {}
    bar_counts: dict = {}

    for c in raw_1min:
        dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        d     = dt.date()
        wday  = dt.weekday()
        t_min = dt.hour * 60 + dt.minute
        is_on_weekday = wday < 5 and t_min < (9 * 60 + 30)
        is_sunday     = wday == 6

        bar_counts[d] = bar_counts.get(d, 0) + 1

        if is_on_weekday:
            on_by_date.setdefault(d, []).append(c)
        elif is_sunday:
            monday = d + timedelta(days=1)
            on_by_date.setdefault(monday, []).append(c)

    target_bars = on_by_date.get(target_date, [])
    tick = _tick_for(symbol)
    vpoc = _compute_vpoc(target_bars, tick)

    # Breakdown by original weekday
    sunday_bars = [c for c in raw_1min
                   if datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET).weekday() == 6]

    return {
        'symbol': symbol,
        'date': date,
        'overnight_bar_count': len(target_bars),
        'overnight_vpoc': vpoc,
        'sunday_bars_total': len(sunday_bars),
        'sunday_dates': sorted(set(
            datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET).date().isoformat()
            for c in sunday_bars
        )),
        'on_dates_available': sorted(d.isoformat() for d in on_by_date),
        'raw_bar_dates': sorted(d.isoformat() for d in bar_counts),
        'first_10_overnight_bars': [
            {
                'time': datetime.fromtimestamp(b['datetime']/1000, tz=timezone.utc).astimezone(ET).strftime('%Y-%m-%d %H:%M'),
                'o': b['open'], 'h': b['high'], 'l': b['low'], 'c': b['close'], 'v': b.get('volume', 0)
            }
            for b in target_bars[:10]
        ],
    }


@app.get('/api/session-vpocs/{symbol:path}')
async def get_session_vpocs(symbol: str):
    """Fetch all available 1-min bars (up to Schwab's limit) and compute per-session VPOCs.
    Also marks each VPOC as 'naked' (price never revisited it in subsequent sessions)
    or 'touched' (price traded at/through it later).

    Useful for building an NVPOC tracker and validating historical key levels.
    """
    symbol = symbol.upper()

    # Serve from cache if fresh
    _vc = _VPOCS_CACHE.get(symbol)
    if _vc and (datetime.now(ET) - _vc['ts']).total_seconds() < _VPOCS_CACHE_TTL:
        return _vc['data']

    tick   = _tick_for(symbol)

    # Try to get as much history as Schwab will give us (request 30 days, get what we can)
    raw_1min = await asyncio.to_thread(get_candles, symbol, 30, 1)

    if not raw_1min:
        return {'symbol': symbol, 'sessions': [], 'bars_fetched': 0, 'error': 'No data returned'}

    # ── Classify bars by RTH session date ──────────────────────────────────────
    rth_by_date: dict = {}
    all_dates_bars: dict = {}   # all bars (RTH + ON) per date for range tracking

    for c in raw_1min:
        dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        d     = dt.date()
        t_min = dt.hour * 60 + dt.minute
        is_rth = dt.weekday() < 5 and (9 * 60 + 30) <= t_min < 16 * 60
        all_dates_bars.setdefault(d, []).append(c)
        if is_rth:
            rth_by_date.setdefault(d, []).append(c)

    sorted_rth_dates = sorted(rth_by_date.keys())   # oldest → newest

    # ── Compute VPOC per session ────────────────────────────────────────────────
    sessions = []
    for d in sorted_rth_dates:
        bars  = rth_by_date[d]
        vpoc  = _compute_vpoc(bars, tick)
        if not vpoc:
            continue

        hi  = max(b['high'] for b in bars)
        lo  = min(b['low']  for b in bars)
        cls = bars[-1]['close']
        vol = sum(b.get('volume', 0) for b in bars)

        sessions.append({
            'date'  : d.isoformat(),
            'vpoc'  : vpoc,
            'high'  : hi,
            'low'   : lo,
            'close' : cls,
            'volume': vol,
            'bars'  : len(bars),
            'naked' : True,   # will be updated below
        })

    # ── Mark VPOCs as naked or touched ─────────────────────────────────────────
    # For each session's VPOC, check whether any *subsequent* session's bars
    # traded at (or crossed through) that price level.
    for i, sess in enumerate(sessions):
        vpoc_price = sess['vpoc']
        touched = False
        for j in range(i + 1, len(sessions)):
            future_bars = rth_by_date.get(sorted_rth_dates[j], [])
            for b in future_bars:
                if b['low'] <= vpoc_price <= b['high']:
                    touched = True
                    sess['touched_on'] = sorted_rth_dates[j].isoformat()
                    break
            if touched:
                break
        sess['naked'] = not touched

    # ── Summary ────────────────────────────────────────────────────────────────
    first_bar_dt = datetime.fromtimestamp(raw_1min[0]['datetime'] / 1000, tz=ET)
    last_bar_dt  = datetime.fromtimestamp(raw_1min[-1]['datetime'] / 1000, tz=ET)

    naked_vpocs  = [s for s in sessions if s['naked']]

    _vpoc_result = {
        'symbol'       : symbol,
        'tick'         : tick,
        'bars_fetched' : len(raw_1min),
        'first_bar'    : first_bar_dt.strftime('%Y-%m-%d %H:%M ET'),
        'last_bar'     : last_bar_dt.strftime('%Y-%m-%d %H:%M ET'),
        'sessions'     : sessions,
        'naked_count'  : len(naked_vpocs),
        'naked_vpocs'  : [{'date': s['date'], 'vpoc': s['vpoc']} for s in naked_vpocs],
    }
    _VPOCS_CACHE[symbol] = {'data': _vpoc_result, 'ts': datetime.now(ET)}
    return _vpoc_result


# ── Stock Profile endpoint ────────────────────────────────────────────────────

@app.get('/api/stock-profile/{ticker}')
def get_stock_profile(ticker: str):
    """Return fundamental profile + live VBH signal + sparkline for a stock ticker.

    Fundamentals/earnings/news come from yfinance (refreshed 6 AM ET daily + startup).
    Signal (entry/stop/target/L1-L4) comes from live state['signals'].
    price_history (daily closes, 90 days) comes from ticker_candles_daily DB.
    """
    ticker = ticker.upper()

    # 1. In-memory first
    profile: dict | None = _STOCK_PROFILES.get(ticker)

    # 2. Supabase fallback — scalar fields only (no earnings_history/news JSON)
    if not profile:
        try:
            from db import get_db as _get_db
            res = _get_db().table('stock_profiles').select('*').eq('ticker', ticker).limit(1).execute()
            if res.data:
                profile = res.data[0]
        except Exception:
            pass

    # 3. Recompute live fields
    sym_entry = next((s for s in state['symbols'] if s['ticker'] == ticker), None)
    sid  = sym_entry['id'] if sym_entry else None
    last = state['last_price'].get(sid) if sid else None

    if profile:
        tp = profile.get('target_price')
        # Live upside % and days-to-earnings recomputed every call
        upside = round((tp - last) / last * 100, 1) if (tp and last) else None
        ned_str = profile.get('next_earnings_date')
        days_to = None
        if ned_str:
            from datetime import date as _date
            ned = _date.fromisoformat(ned_str)
            days_to = (ned - datetime.now(ET).date()).days
        profile = {**profile, 'upside_pct': upside, 'days_to_earnings': days_to}

    # 4. All VBH signals for this ticker (all models — AGG / CON / WIDE)
    all_sigs = [s for s in state['signals'] if s['symbol'] == ticker]
    signal   = all_sigs[0] if all_sigs else None   # backward compat

    # 5. Daily closes for 90-day sparkline
    price_history: list[dict] = []
    try:
        from db import get_daily_candles_db as _daily_db
        bars = _daily_db(ticker, 90)
        price_history = [{'date': b['bar_date'], 'close': float(b['close'])} for b in bars]
    except Exception:
        pass

    return {
        'ticker':        ticker,
        'profile':       profile,
        'signal':        signal,        # first signal — backward compat
        'signals':       all_sigs,      # all models for this ticker
        'last':          last,
        'price_history': price_history,
    }


# ── Entry Log ─────────────────────────────────────────────────────────────────

@app.get('/api/entry-log')
async def api_entry_log(
    limit: int  = Query(default=200, le=1000),
    model: str  = Query(default='all'),   # 'all' | 'AGG' | 'CON' | 'WIDE' | 'CR'
    side:  str  = Query(default='all'),   # 'all' | 'LONG' | 'SHORT'
):
    """Return recent ENTRY alert history for forward-testing analysis.

    Each row is a NEAR→ENTRY transition across all models (AGG/CON/WIDE/CR).
    Newest first. Default 200 rows, max 1000.
    Filter by model=CR to see only Clearing Range events.
    """
    try:
        rows = await asyncio.to_thread(get_entry_log, limit, model, side)
        return {'entries': rows, 'count': len(rows)}
    except Exception as e:
        log.warning('entry-log fetch error: %s', e)
        return {'entries': [], 'count': 0, 'error': str(e)}


@app.delete('/api/entry-log')
async def api_entry_log_clear():
    """Purge all entry_log rows (used to reset forward-testing history)."""
    try:
        deleted = await asyncio.to_thread(clear_entry_log)
        log.info('Entry log purged — %d rows deleted', deleted)
        return {'deleted': deleted}
    except Exception as e:
        log.warning('entry-log clear error: %s', e)
        return {'deleted': 0, 'error': str(e)}


# ── Asset Personality: hour score for all active symbols ─────────────────────
@app.get('/api/personality')
async def api_personality():
    """
    Returns asset personality hour scores for all symbols in asset_personality
    at the current ET hour.  Used by the dashboard grid to show Hot/Good/Neutral/Avoid
    buttons per model (AGG / CON / WIDE) on every row.

    Response shape:
      {
        hour_et: 14,
        session: "RTH",
        data: {
          "/ES":  { AGG: {...}, CON: {...}, WIDE: {...} },
          "AAPL": { AGG: {...}, CON: {...}, WIDE: {...} },
          ...
        }
      }
    """
    et_now   = datetime.now(ET)
    et_hour  = et_now.hour
    session  = 'RTH' if 9 <= et_hour <= 15 else 'OFF'

    def _fetch():
        from db import get_db
        db = get_db()

        # All personality rows for current hour
        rows = (db.table('asset_personality')
                  .select('symbol_id,model,signal_strength,direction_bias,'
                          'win_rate,avg_pnl_usd,net_pnl_usd,total_trades,'
                          'long_win_rate,short_win_rate,long_net_usd,short_net_usd,session')
                  .eq('hour_et', et_hour)
                  .execute())

        # Build ticker → id map
        syms     = db.table('symbols').select('id,ticker').execute()
        id_to_tk = {r['id']: r['ticker'] for r in syms.data}

        result: dict = {}
        for row in rows.data:
            ticker = id_to_tk.get(row['symbol_id'])
            if not ticker:
                continue
            if ticker not in result:
                result[ticker] = {}
            result[ticker][row['model']] = {
                'signal_strength': row['signal_strength'],   # STRONG|MODERATE|WEAK|DEAD
                'direction_bias':  row['direction_bias'],    # LONG|SHORT|NEUTRAL|AVOID
                'win_rate':        row['win_rate'],
                'avg_pnl_usd':     row['avg_pnl_usd'],
                'net_pnl_usd':     row['net_pnl_usd'],
                'total_trades':    row['total_trades'],
                'long_win_rate':   row['long_win_rate'],
                'short_win_rate':  row['short_win_rate'],
                'long_net_usd':    row['long_net_usd'],
                'short_net_usd':   row['short_net_usd'],
                'session':         row['session'],
            }
        return result

    try:
        data = await asyncio.to_thread(_fetch)
        return {'hour_et': et_hour, 'session': session, 'data': data}
    except Exception as e:
        log.warning('personality fetch error: %s', e)
        return {'hour_et': et_hour, 'session': session, 'data': {}, 'error': str(e)}


@app.get('/api/personality/{ticker:path}')
async def api_personality_symbol(ticker: str):
    """
    Returns all-hours asset personality data for a single ticker.
    Used by the Futures Panel "Profile" tab to display the full
    hourly trading profile across RTH and overnight sessions.

    Response shape:
      {
        ticker: "/ES",
        current_hour_et: 14,
        data: {
          "9":  { "AGG": {...}, "CON": {...}, "WIDE": {...} },
          "10": { "AGG": {...}, "CON": {...}, "WIDE": {...} },
          ...
        }
      }
    """
    # Strip mini-contract suffix so /MES → /ES lookups work
    clean = ticker.replace('%2F', '/').split(':')[0]

    et_now       = datetime.now(ET)
    current_hour = et_now.hour

    def _fetch():
        from db import get_db
        db = get_db()

        # Find symbol_id for this ticker (try exact, then strip leading /)
        sym_row = db.table('symbols').select('id,ticker').eq('ticker', clean).execute()
        if not sym_row.data:
            return {}

        sid = sym_row.data[0]['id']

        rows = (db.table('asset_personality')
                  .select('hour_et,model,signal_strength,direction_bias,'
                          'win_rate,avg_pnl_usd,net_pnl_usd,total_trades,'
                          'long_win_rate,short_win_rate,long_net_usd,short_net_usd,session')
                  .eq('symbol_id', sid)
                  .execute())

        result: dict = {}
        for row in rows.data:
            h = str(row['hour_et'])
            if h not in result:
                result[h] = {}
            result[h][row['model']] = {
                'signal_strength': row['signal_strength'],
                'direction_bias':  row['direction_bias'],
                'win_rate':        row['win_rate'],
                'avg_pnl_usd':     row['avg_pnl_usd'],
                'net_pnl_usd':     row['net_pnl_usd'],
                'total_trades':    row['total_trades'],
                'long_win_rate':   row['long_win_rate'],
                'short_win_rate':  row['short_win_rate'],
                'long_net_usd':    row['long_net_usd'],
                'short_net_usd':   row['short_net_usd'],
                'session':         row['session'],
            }
        return result

    try:
        data = await asyncio.to_thread(_fetch)
        return {'ticker': clean, 'current_hour_et': current_hour, 'data': data}
    except Exception as e:
        log.warning('personality/%s fetch error: %s', clean, e)
        return {'ticker': clean, 'current_hour_et': current_hour, 'data': {}, 'error': str(e)}


# ── Market Profile helpers ─────────────────────────────────────────────────────

def _build_rth_tpo_profile(bars: list[dict], tick: float) -> dict:
    """Build a full RTH TPO letter profile from 1-min bars.

    Letters: A = 9:30–10:00, B = 10:00–10:30, … M = 15:30–16:00 (13 periods).
    Each 30-min period contributes exactly 1 count per tick it touched.

    Returns:
        profile         – [{price, letters, count}] sorted high→low
        poc             – price with most period hits
        vah / val       – 70 % value area boundaries
        single_prints   – prices touched by exactly 1 period (poor structure)
        ib_high/ib_low  – Initial Balance range (A + B periods)
        ib_range        – ib_high - ib_low
        periods         – number of 30-min periods with at least 1 bar
        period_ranges   – {letter: {high, low}} for each completed period
        session_high    – highest high of all RTH bars
        session_low     – lowest  low  of all RTH bars
    """
    from collections import defaultdict

    RTH_START = 9 * 60 + 30   # 570 min since midnight

    price_letters: dict = defaultdict(set)
    period_ranges: dict = {}
    period_last_dt: dict = {}          # letter → latest bar datetime (for close tracking)
    open_price: float | None = None
    open_dt:    int   | None = None

    for b in bars:
        dt    = datetime.fromtimestamp(b['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        t_min = dt.hour * 60 + dt.minute
        if not (RTH_START <= t_min < 16 * 60):
            continue
        period_idx = (t_min - RTH_START) // 30
        if not (0 <= period_idx <= 12):
            continue
        letter = chr(ord('A') + period_idx)
        # Track opening price (first RTH bar chronologically)
        if open_dt is None or b['datetime'] < open_dt:
            open_dt    = b['datetime']
            open_price = b['open']

        lo_t = round(round(b['low']  / tick) * tick, 6)
        hi_t = round(round(b['high'] / tick) * tick, 6)
        p = lo_t
        while p <= hi_t + tick * 0.001:
            price_letters[round(p, 6)].add(letter)
            p = round(p + tick, 6)

        if letter not in period_ranges:
            period_ranges[letter]  = {'high': b['high'], 'low': b['low'], 'close': b['close']}
            period_last_dt[letter] = b['datetime']
        else:
            period_ranges[letter]['high'] = max(period_ranges[letter]['high'], b['high'])
            period_ranges[letter]['low']  = min(period_ranges[letter]['low'],  b['low'])
            if b['datetime'] > period_last_dt.get(letter, 0):
                period_ranges[letter]['close'] = b['close']
                period_last_dt[letter]         = b['datetime']

    _empty = {
        'profile': [], 'poc': None, 'vah': None, 'val': None,
        'single_prints': [], 'ib_high': None, 'ib_low': None, 'ib_range': None,
        'periods': 0, 'period_ranges': {}, 'session_high': None, 'session_low': None,
        'open_price': None,
    }
    if not price_letters:
        return _empty

    tpo_map: dict[float, int] = {p: len(ls) for p, ls in price_letters.items()}
    total   = sum(tpo_map.values())
    target  = total * 0.70

    poc     = max(tpo_map, key=tpo_map.get)
    prices  = sorted(tpo_map.keys())
    poc_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - poc))

    va_set  = {prices[poc_idx]}
    va_tpos = tpo_map[prices[poc_idx]]
    lo_idx  = poc_idx
    hi_idx  = poc_idx
    while va_tpos < target:
        can_up   = hi_idx + 1 < len(prices)
        can_down = lo_idx - 1 >= 0
        if not can_up and not can_down:
            break
        up_cnt = tpo_map[prices[hi_idx + 1]] if can_up   else -1
        dn_cnt = tpo_map[prices[lo_idx - 1]] if can_down else -1
        if up_cnt >= dn_cnt:
            hi_idx += 1; va_set.add(prices[hi_idx]); va_tpos += up_cnt
        else:
            lo_idx -= 1; va_set.add(prices[lo_idx]); va_tpos += dn_cnt

    # IB: A + B periods
    ib_high = ib_low = None
    for ltr in ('A', 'B'):
        if ltr in period_ranges:
            pr = period_ranges[ltr]
            ib_high = pr['high'] if ib_high is None else max(ib_high, pr['high'])
            ib_low  = pr['low']  if ib_low  is None else min(ib_low,  pr['low'])

    profile = sorted(
        [{'price': round(p, 2), 'letters': ''.join(sorted(ls)), 'count': len(ls)}
         for p, ls in price_letters.items()],
        key=lambda x: x['price'], reverse=True
    )
    session_high = max(r['price'] for r in profile) if profile else None
    session_low  = min(r['price'] for r in profile) if profile else None

    return {
        'profile':       profile,
        'poc':           round(poc, 2),
        'vah':           round(max(va_set), 2),
        'val':           round(min(va_set), 2),
        'single_prints': sorted([round(p, 2) for p, c in tpo_map.items() if c == 1], reverse=True),
        'ib_high':       round(ib_high, 2) if ib_high else None,
        'ib_low':        round(ib_low,  2) if ib_low  else None,
        'ib_range':      round(ib_high - ib_low, 2) if ib_high and ib_low else None,
        'periods':       len(period_ranges),
        'period_ranges': {k: {'high': v['high'], 'low': v['low'], 'close': v.get('close')}
                          for k, v in period_ranges.items()},
        'session_high':  session_high,
        'session_low':   session_low,
        'open_price':    round(open_price, 2) if open_price is not None else None,
    }


def _build_overnight_tpo_profile(bars: list[dict], tick: float) -> dict:
    """Build overnight TPO letter profile using lowercase letters + numerals.

    31 thirty-minute periods from 6:00 PM to 9:30 AM ET:
        a–l  → 6:00 PM – midnight   (period 0–11,  t_min 1080–1440)
        m–z  → midnight – 7:00 AM   (period 12–25, t_min 0–420)
        1–5  → 7:00 AM – 9:30 AM   (period 26–30, t_min 420–570)

    Returns same structure as _build_rth_tpo_profile (minus ib_* fields).
    """
    from collections import defaultdict

    ON_LETTERS = list('abcdefghijklmnopqrstuvwxyz') + ['1', '2', '3', '4', '5']

    price_letters: dict = defaultdict(set)
    period_ranges: dict = {}

    for b in bars:
        dt    = datetime.fromtimestamp(b['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        t_min = dt.hour * 60 + dt.minute

        if t_min >= 18 * 60:          # evening: 6:00 PM – midnight
            period_idx = (t_min - 18 * 60) // 30
        elif t_min < 9 * 60 + 30:     # pre-market: midnight – 9:30 AM
            period_idx = 12 + t_min // 30
        else:
            continue                   # RTH bar — skip

        if not (0 <= period_idx <= 30):
            continue
        letter = ON_LETTERS[period_idx]

        lo_t = round(round(b['low']  / tick) * tick, 6)
        hi_t = round(round(b['high'] / tick) * tick, 6)
        p = lo_t
        while p <= hi_t + tick * 0.001:
            price_letters[round(p, 6)].add(letter)
            p = round(p + tick, 6)

        if letter not in period_ranges:
            period_ranges[letter] = {'high': b['high'], 'low': b['low']}
        else:
            period_ranges[letter]['high'] = max(period_ranges[letter]['high'], b['high'])
            period_ranges[letter]['low']  = min(period_ranges[letter]['low'],  b['low'])

    _empty = {
        'profile': [], 'poc': None, 'vah': None, 'val': None,
        'single_prints': [], 'periods': 0, 'period_ranges': {},
        'session_high': None, 'session_low': None,
    }
    if not price_letters:
        return _empty

    tpo_map: dict[float, int] = {p: len(ls) for p, ls in price_letters.items()}
    total   = sum(tpo_map.values())
    target  = total * 0.70

    poc     = max(tpo_map, key=tpo_map.get)
    prices  = sorted(tpo_map.keys())
    poc_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - poc))

    va_set  = {prices[poc_idx]}
    va_tpos = tpo_map[prices[poc_idx]]
    lo_idx  = poc_idx
    hi_idx  = poc_idx
    while va_tpos < target:
        can_up   = hi_idx + 1 < len(prices)
        can_down = lo_idx - 1 >= 0
        if not can_up and not can_down:
            break
        up_cnt = tpo_map[prices[hi_idx + 1]] if can_up   else -1
        dn_cnt = tpo_map[prices[lo_idx - 1]] if can_down else -1
        if up_cnt >= dn_cnt:
            hi_idx += 1; va_set.add(prices[hi_idx]); va_tpos += up_cnt
        else:
            lo_idx -= 1; va_set.add(prices[lo_idx]); va_tpos += dn_cnt

    idx_of = {ltr: i for i, ltr in enumerate(ON_LETTERS)}
    profile = sorted(
        [{'price': round(p, 2),
          'letters': ''.join(sorted(ls, key=lambda x: idx_of.get(x, 99))),
          'count': len(ls)}
         for p, ls in price_letters.items()],
        key=lambda x: x['price'], reverse=True
    )
    session_high = max(r['price'] for r in profile) if profile else None
    session_low  = min(r['price'] for r in profile) if profile else None

    return {
        'profile':       profile,
        'poc':           round(poc, 2),
        'vah':           round(max(va_set), 2),
        'val':           round(min(va_set), 2),
        'single_prints': sorted([round(p, 2) for p, c in tpo_map.items() if c == 1], reverse=True),
        'periods':       len(period_ranges),
        'period_ranges': {k: {'high': v['high'], 'low': v['low']} for k, v in period_ranges.items()},
        'session_high':  session_high,
        'session_low':   session_low,
    }


def _classify_opening(open_price: float, ib_high: float | None, ib_low: float | None,
                      prior_vah: float | None, prior_val: float | None,
                      prior_poc: float | None, tick: float) -> dict:
    """Classify opening type: OA / OD / OTD / ORR.

    Uses IB high/low (A+B combined) so the classification is based on the full
    initial balance, not just the first 30-minute A period alone.
    """
    if prior_vah is None or prior_val is None:
        return {'type': 'UNKNOWN', 'label': 'Unknown',
                'description': 'No prior session data.', 'inside_prior_va': None}

    inside_va = prior_val <= open_price <= prior_vah
    above_va  = open_price > prior_vah

    vs_vah = round(open_price - prior_vah, 2)
    vs_val = round(open_price - prior_val, 2)
    vs_poc = round(open_price - prior_poc, 2) if prior_poc else None

    base = {'inside_prior_va': inside_va, 'vs_prior_vah': vs_vah,
            'vs_prior_val': vs_val, 'vs_prior_poc': vs_poc}

    if ib_high is None or ib_low is None:
        # No bars yet — pre-open context only
        if inside_va:
            return {**base, 'type': 'OA', 'label': 'Open Auction',
                    'description': f'Opened inside prior value ({prior_val:.2f}–{prior_vah:.2f}). '
                                   f'Two-sided auction likely. 80% rule activates if A+B stay inside VA.'}
        loc = 'above' if above_va else 'below'
        ref = prior_vah if above_va else prior_val
        return {**base, 'type': 'PENDING', 'label': f'Outside Value ({loc.capitalize()})',
                'description': f'Opened {"above VAH" if above_va else "below VAL"} ({ref:.2f}). '
                               f'Watching IB for OD confirmation or ORR failure.'}

    if inside_va:
        return {**base, 'type': 'OA', 'label': 'Open Auction',
                'description': f'Opened inside prior value ({prior_val:.2f}–{prior_vah:.2f}). '
                               f'Two-sided auction. Buy VAL / sell VAH until a clear break.'}

    if above_va:
        # OTD ↑: IB tested VAH from above (barely dipped to/near it) then drove higher.
        if prior_vah <= ib_low <= prior_vah + 2 * tick:
            return {**base, 'type': 'OTD', 'label': 'Open Test Drive ↑',
                    'description': f'Opened above VAH ({prior_vah:.2f}), IB tested it then drove higher. '
                                   f'Bullish. Buy pullbacks to {prior_vah:.2f}.'}
        # ORR ↓: IB reversed back THROUGH VAH into prior value area.
        if ib_low < prior_vah:
            return {**base, 'type': 'ORR', 'label': 'Open Rejection Reverse ↓',
                    'description': f'Opened above VAH ({prior_vah:.2f}) but IB reversed back through it. Bearish. '
                                   f'Sell rallies to {prior_vah:.2f}, target VAL {prior_val:.2f}.'}
        return {**base, 'type': 'OD', 'label': 'Open Drive ↑',
                'description': f'Opened above VAH ({prior_vah:.2f}) and drove higher. '
                               f'One-timeframe buyers in control — do not fade. Trail stops.'}

    # Below VA
    # OTD ↓: IB tested VAL from below (barely rose to/near it) then drove lower.
    if prior_val - 2 * tick <= ib_high <= prior_val:
        return {**base, 'type': 'OTD', 'label': 'Open Test Drive ↓',
                'description': f'Opened below VAL ({prior_val:.2f}), IB tested it then drove lower. '
                               f'Bearish. Sell bounces to {prior_val:.2f}.'}
    # ORR ↑: IB reversed back THROUGH VAL into prior value area.
    if ib_high > prior_val:
        return {**base, 'type': 'ORR', 'label': 'Open Rejection Reverse ↑',
                'description': f'Opened below VAL ({prior_val:.2f}) but IB reversed back through it. Bullish. '
                               f'Buy pullbacks to {prior_val:.2f}, target VAH {prior_vah:.2f}.'}
    return {**base, 'type': 'OD', 'label': 'Open Drive ↓',
            'description': f'Opened below VAL ({prior_val:.2f}) and drove lower. '
                           f'One-timeframe sellers in control — do not fade. Trail shorts.'}


def _classify_day_type(today_prof: dict, prior_rth_range: float | None) -> dict:
    """Classify day type after IB is complete (>=2 periods)."""
    periods  = today_prof.get('periods', 0)
    ib_high  = today_prof.get('ib_high')
    ib_low   = today_prof.get('ib_low')
    ib_range = today_prof.get('ib_range')
    s_high   = today_prof.get('session_high')
    s_low    = today_prof.get('session_low')
    vah      = today_prof.get('vah')
    val      = today_prof.get('val')

    if periods < 2 or not ib_high or not ib_low:
        return {'type': 'DEVELOPING', 'label': 'IB Building',
                'description': 'Initial Balance not yet complete (10:00–10:30 AM ET). '
                               'Day type determined after the B period closes.'}

    ext_up   = max(0.0, (s_high or ib_high) - ib_high)
    ext_down = max(0.0, ib_low - (s_low or ib_low))
    total_r  = (s_high or ib_high) - (s_low or ib_low)

    typical = prior_rth_range or ib_range or 1
    ib_ratio = round(ib_range / typical, 2) if typical else 1

    both = ext_up > 0.0 and ext_down > 0.0
    skew = abs(ext_up - ext_down) > ib_range * 0.35 if ib_range else False

    extra = {'ib_range': ib_range, 'ext_up': round(ext_up, 2),
             'ext_down': round(ext_down, 2), 'ib_ratio': ib_ratio}

    if total_r > ib_range * 2.5 and (ext_up > ib_range * 1.5 or ext_down > ib_range * 1.5):
        d = '↑' if ext_up > ext_down else '↓'
        side = 'buyers' if ext_up > ext_down else 'sellers'
        return {**extra, 'type': 'TREND', 'label': f'Trend Day {d}',
                'description': f'Total range {total_r:.2f} vs IB {ib_range:.2f}. '
                               f'One-timeframe {side} in control. Do NOT fade — trail stops and hold.'}
    if both and skew:
        d = '↑' if ext_up > ext_down else '↓'
        return {**extra, 'type': 'NEUTRAL_EXTREME', 'label': f'Neutral Extreme {d}',
                'description': f'Extended both directions but closed near {"high" if ext_up > ext_down else "low"}. '
                               f'Watch close location — hints at tomorrow\'s opening bias.'}
    if both:
        return {**extra, 'type': 'NEUTRAL', 'label': 'Neutral Day',
                'description': f'Extended both above (+{ext_up:.2f}) and below (-{ext_down:.2f}) IB. '
                               f'No clear edge. Be selective — wait for late-session resolution.'}
    if ext_up > ib_range * 0.25 and ext_up > ext_down:
        return {**extra, 'type': 'NORMAL_VAR_UP', 'label': 'Normal Variation ↑',
                'description': f'IB moderate, extended {ext_up:.2f} above. '
                               f'Buyers won the IB auction. Buy pullbacks to IB High ({ib_high:.2f}).'}
    if ext_down > ib_range * 0.25 and ext_down > ext_up:
        return {**extra, 'type': 'NORMAL_VAR_DOWN', 'label': 'Normal Variation ↓',
                'description': f'IB moderate, extended {ext_down:.2f} below. '
                               f'Sellers won the IB auction. Sell rallies to IB Low ({ib_low:.2f}).'}
    return {**extra, 'type': 'NORMAL', 'label': 'Normal Day',
            'description': f'Wide IB ({ib_range:.2f}), balanced two-sided auction. '
                           f'Buy near VAL ({f"{val:.2f}" if val else "—"}), sell near VAH ({f"{vah:.2f}" if vah else "—"}).'}


def _check_80pct_rule(open_price: float, a_period: dict | None, b_period: dict | None,
                      prior_vah: float | None, prior_val: float | None,
                      current_price: float | None) -> dict:
    """80% rule: open inside prior VA + A+B both inside VA → 80% probability of VA fill."""
    if prior_vah is None or prior_val is None:
        return {'triggered': False, 'description': 'Prior session data unavailable.'}
    inside_va = prior_val <= open_price <= prior_vah
    if not inside_va:
        return {'triggered': False,
                'description': f'Open ({open_price:.2f}) outside prior VA — rule not applicable.'}
    if a_period is None:
        return {'triggered': False,
                'description': 'A period not yet complete. Rule activates if A+B both stay inside prior VA.'}
    a_inside = a_period['low'] >= prior_val and a_period['high'] <= prior_vah
    if b_period is None:
        return {'triggered': False, 'a_inside': a_inside,
                'description': f'A period {"✓ inside" if a_inside else "✗ outside"} VA. Waiting for B close.'}
    b_inside = b_period['low'] >= prior_val and b_period['high'] <= prior_vah
    if not (a_inside and b_inside):
        reason = 'A' if not a_inside else 'B'
        return {'triggered': False,
                'description': f'{reason} period broke outside prior VA — rule NOT triggered.'}
    # Triggered
    mid_va    = (prior_vah + prior_val) / 2
    direction = 'SHORT' if open_price >= mid_va else 'LONG'
    target    = prior_val if direction == 'SHORT' else prior_vah
    hit       = (current_price is not None and
                 ((direction == 'LONG'  and current_price >= target) or
                  (direction == 'SHORT' and current_price <= target)))
    label     = f'Target {"REACHED" if hit else "PENDING"}: {target:.2f}'
    desc      = (f'Rule 80% TRIGGERED {"↓" if direction == "SHORT" else "↑"} — '
                 f'open in {"upper" if direction == "SHORT" else "lower"} half of VA, '
                 f'A+B both inside. 80% probability of reaching '
                 f'{"VAL" if direction == "SHORT" else "VAH"} ({target:.2f}).')
    if hit:
        desc += ' Target already reached.'
    return {'triggered': True, 'direction': direction, 'target': target,
            'already_hit': hit, 'label': label, 'description': desc}


def _generate_ib_signals(session_prof: dict, session_overnight: dict,
                          prior_rth: dict, tick: float,
                          now_et=None) -> dict:
    """Generate actionable signals after IB is complete (B period closed at 10:30 AM ET).

    Reads Thursday overnight → Friday IB (or any ON → RTH pair) and applies Dalton's
    framework to produce a bias, trade plan, and ranked key levels.

    Dalton Open Types incorporated:
      Open Auction (OA)   — open INSIDE overnight range; overnight traders in control.
                            Two-sided expected: buy VAL / sell VAH until clear break.
      Open above ONH      — day-session participants already extended; directional from bell.
      Open below ONL      — same logic, bearish direction.

    IB excess from an OA open is treated as a "probe" (+1) rather than a confirmed
    extension (+2). Both signals must align for a high-conviction directional call.
    """
    periods  = session_prof.get('periods', 0)
    ib_high  = session_prof.get('ib_high')
    ib_low   = session_prof.get('ib_low')
    ib_range = session_prof.get('ib_range')
    open_px  = session_prof.get('open_price')   # first RTH bar open

    # B period only closes at 10:30 AM ET. period_ranges gains a 'B' key the
    # moment the first 5-min bar of B prints (10:05), which makes periods == 2
    # before B is actually complete.  Guard against premature analysis with an
    # explicit time check when now_et is provided.
    if now_et is not None:
        t_min = now_et.hour * 60 + now_et.minute
        if t_min < 10 * 60 + 30:   # before 10:30 AM ET
            return {'ready': False,
                    'description': 'IB not complete — signals available after 10:30 AM ET.'}

    if periods < 2 or not ib_high or not ib_low:
        return {'ready': False,
                'description': 'IB not complete — signals available after 10:30 AM ET.'}

    on_high = session_overnight.get('high')
    on_low  = session_overnight.get('low')
    on_poc  = session_overnight.get('poc')
    on_vah  = session_overnight.get('vah')
    on_val  = session_overnight.get('val')
    on_pr   = session_overnight.get('period_ranges', {})

    p_vah   = prior_rth.get('vah')
    p_val   = prior_rth.get('val')
    p_poc   = prior_rth.get('poc')
    p_high  = prior_rth.get('session_high') or prior_rth.get('high')
    p_low   = prior_rth.get('session_low')  or prior_rth.get('low')

    signals: list[dict] = []
    key_levels: list[dict] = []
    bias_score = 0   # +ve = bullish, -ve = bearish

    # Convenience strings for optional levels
    vah_s   = f'{on_vah:.2f}' if on_vah is not None else 'n/a'
    val_s   = f'{on_val:.2f}' if on_val is not None else 'n/a'
    on_poc_s= f'{on_poc:.2f}' if on_poc is not None else 'n/a'

    # ── 0. Opening context vs overnight range ─────────────────────────────────
    # This is the foundational Dalton Open Type classification.
    open_inside_on = (open_px is not None and on_low is not None and on_high is not None
                      and on_low - tick <= open_px <= on_high + tick)
    open_above_onh = (open_px is not None and on_high is not None
                      and open_px > on_high + tick)
    open_below_onl = (open_px is not None and on_low is not None
                      and open_px < on_low - tick)

    if open_inside_on:
        signals.append({'type': 'NEUTRAL', 'signal': 'OA open — overnight traders in control',
            'detail': (f'Open ({open_px:.2f}) was inside the overnight range '
                       f'({on_low:.2f}–{on_high:.2f}). Overnight traders still positioned — '
                       f'two-sided auction expected. Buy overnight VAL ({val_s}), '
                       f'sell overnight VAH ({vah_s}) until a decisive break occurs.')})
    elif open_above_onh and on_high is not None:
        bias_score += 1
        signals.append({'type': 'BULLISH', 'signal': 'Open above overnight range',
            'detail': (f'Open ({open_px:.2f}) gapped {open_px - on_high:.2f} pts above '
                       f'ONH ({on_high:.2f}). Day-session participants in control from the open — '
                       f'directional bullish context established before the IB forms.')})
    elif open_below_onl and on_low is not None:
        bias_score -= 1
        signals.append({'type': 'BEARISH', 'signal': 'Open below overnight range',
            'detail': (f'Open ({open_px:.2f}) gapped {on_low - open_px:.2f} pts below '
                       f'ONL ({on_low:.2f}). Day-session sellers in control from the open — '
                       f'directional bearish context established before the IB forms.')})

    # ── 1. IB vs Overnight Range ──────────────────────────────────────────────
    ib_above_onh = on_high is not None and ib_high > on_high + tick
    ib_below_onl = on_low  is not None and ib_low  < on_low  - tick
    ib_in_on     = (on_high is not None and on_low is not None
                    and ib_high <= on_high + tick and ib_low >= on_low - tick)

    b_close = session_prof.get('period_ranges', {}).get('B', {}).get('close')

    # Probe-rejection flags — set below when probe is confirmed rejected
    onh_probe_rejected = False
    onl_probe_rejected = False

    if ib_above_onh and not ib_below_onl:
        if open_inside_on:
            # OA open + IB pushed above ONH.
            # Key question: did B *close* above ONH (acceptance) or back inside (probe)?
            b_accepted_above_onh = b_close is not None and b_close > on_high + tick
            if b_accepted_above_onh:
                # B closed above ONH → buyers proved themselves despite OA open context.
                # Treat as confirmed excess: same weight as a directional open.
                bias_score += 2
                excess_pts = round(b_close - on_high, 2)
                signals.append({'type': 'BULLISH', 'signal': 'IB accepted above ONH — OA open confirmed',
                    'detail': (f'Open ({open_px:.2f}) was inside the overnight range (OA), but '
                               f'buyers pushed the IB High to {ib_high:.2f} and — critically — B period '
                               f'closed at {b_close:.2f}, {excess_pts} pts above ONH ({on_high:.2f}). '
                               f'Acceptance is confirmed: overnight sellers could not hold their high. '
                               f'ONH ({on_high:.2f}) is now support — buy pullbacks there. '
                               f'Stop below ON POC ({on_poc_s}).')})
                key_levels.append({'level': on_high, 'label': 'ONH → Support (confirmed)', 'role': 'support', 'color': 'green'})
            else:
                # B closed back inside the overnight range — probe only, conviction absent.
                # Overnight sellers held their ground → reverts to two-sided OA context.
                bias_score += 1
                onh_probe_rejected = True
                signals.append({'type': 'NEUTRAL', 'signal': 'IB probed above ONH — B closed back inside (OA)',
                    'detail': (f'IB High ({ib_high:.2f}) extended {round(ib_high - on_high, 2)} pts above '
                               f'ONH ({on_high:.2f}), but B period closed at '
                               f'{f"{b_close:.2f}" if b_close is not None else "n/a"} — back inside the overnight range. '
                               f'The probe was rejected: overnight sellers held their high. '
                               f'Overnight traders are still in control — treat as two-sided OA: '
                               f'buy overnight VAL ({val_s}), sell overnight VAH ({vah_s}). '
                               f'Watch C period: a close above ONH ({on_high:.2f}) would confirm belated acceptance.')})
                key_levels.append({'level': on_high, 'label': 'ONH — watch for C close above', 'role': 'pivot', 'color': 'amber'})
        else:
            # Open already above ONH → clean directional excess, no ambiguity.
            bias_score += 2
            signals.append({'type': 'BULLISH', 'signal': 'IB excess above ONH',
                'detail': (f'IB High ({ib_high:.2f}) extended {round(ib_high - on_high, 2)} pts above '
                           f'Overnight High ({on_high:.2f}). Day-session buyers rejected overnight sellers. '
                           f'ONH ({on_high:.2f}) flips to first support — buy pullbacks there. '
                           f'Stop below ON POC ({on_poc_s}).')})
            key_levels.append({'level': on_high, 'label': 'ONH → Support', 'role': 'support', 'color': 'green'})

    elif ib_below_onl and not ib_above_onh:
        if open_inside_on:
            b_accepted_below_onl = b_close is not None and b_close < on_low - tick
            if b_accepted_below_onl:
                # B closed below ONL → sellers confirmed control from OA open.
                bias_score -= 2
                pts_below = round(on_low - b_close, 2)
                signals.append({'type': 'BEARISH', 'signal': 'IB accepted below ONL — OA open confirmed',
                    'detail': (f'Open ({open_px:.2f}) was inside the overnight range (OA), but '
                               f'sellers pushed the IB Low to {ib_low:.2f} and B period '
                               f'closed at {b_close:.2f}, {pts_below} pts below ONL ({on_low:.2f}). '
                               f'Acceptance is confirmed: overnight buyers could not hold their low. '
                               f'ONL ({on_low:.2f}) is now resistance — sell rallies there. '
                               f'Stop above ON POC ({on_poc_s}).')})
                key_levels.append({'level': on_low, 'label': 'ONL → Resistance (confirmed)', 'role': 'resistance', 'color': 'red'})
            else:
                # B closed back inside — probe only.
                # Overnight buyers held their ground → reverts to two-sided OA context.
                bias_score -= 1
                onl_probe_rejected = True
                signals.append({'type': 'NEUTRAL', 'signal': 'IB probed below ONL — B closed back inside (OA)',
                    'detail': (f'IB Low ({ib_low:.2f}) dropped {round(on_low - ib_low, 2)} pts below '
                               f'ONL ({on_low:.2f}), but B period closed at '
                               f'{f"{b_close:.2f}" if b_close is not None else "n/a"} — back inside the overnight range. '
                               f'The probe was rejected: overnight buyers held their low. '
                               f'Overnight traders are still in control — treat as two-sided OA: '
                               f'buy overnight VAL ({val_s}), sell overnight VAH ({vah_s}). '
                               f'Watch C period: a close below ONL ({on_low:.2f}) would confirm belated acceptance.')})
                key_levels.append({'level': on_low, 'label': 'ONL — watch for C close below', 'role': 'pivot', 'color': 'amber'})
        else:
            bias_score -= 2
            signals.append({'type': 'BEARISH', 'signal': 'IB excess below ONL',
                'detail': (f'IB Low ({ib_low:.2f}) extended {round(on_low - ib_low, 2)} pts below '
                           f'Overnight Low ({on_low:.2f}). Day-session sellers rejected overnight buyers. '
                           f'ONL ({on_low:.2f}) flips to first resistance — sell rallies there.')})
            key_levels.append({'level': on_low, 'label': 'ONL → Resistance', 'role': 'resistance', 'color': 'red'})

    elif ib_above_onh and ib_below_onl:
        signals.append({'type': 'NEUTRAL', 'signal': 'IB contains entire overnight range',
            'detail': (f'IB ({ib_low:.2f}–{ib_high:.2f}) absorbed the full overnight range '
                       f'({on_low:.2f}–{on_high:.2f}). Wide IB, both sides active. '
                       f'Neutral day likely — rotate within IB until a late close outside.')})
        key_levels.append({'level': on_high, 'label': 'ONH',  'role': 'pivot', 'color': 'amber'})
        key_levels.append({'level': on_low,  'label': 'ONL',  'role': 'pivot', 'color': 'amber'})

    elif ib_in_on:
        signals.append({'type': 'NEUTRAL', 'signal': 'IB contained within overnight range',
            'detail': (f'IB ({ib_low:.2f}–{ib_high:.2f}) stays inside the overnight range '
                       f'({on_low:.2f}–{on_high:.2f}). RTH accepting overnight prices — rotational day. '
                       f'Sell near ONH ({on_high:.2f}), buy near ONL ({on_low:.2f}) until a clear breakout.')})
        key_levels.append({'level': on_high, 'label': 'ONH — fade (sell) / long target', 'role': 'target_up',   'color': 'cyan'})
        key_levels.append({'level': on_low,  'label': 'ONL — long entry / fade (buy)',  'role': 'target_down', 'color': 'cyan'})

    # ── 2. IB vs Overnight POC ────────────────────────────────────────────────
    # Use B period close (last bar of 10:00–10:30 window) to distinguish between:
    #   • ON POC acting as support (IB touched it but B closed well above) → bullish
    #   • ON POC acting as resistance (IB touched it but B closed well below) → bearish
    #   • Genuine straddle (B closed near ON POC — true indecision)
    # (b_close already defined above)
    straddle_thresh = 3 * tick   # within 3 ticks = genuine indecision

    if on_poc is not None:
        if ib_low > on_poc + tick:
            # Entire IB above ON POC
            bias_score += 1
            signals.append({'type': 'BULLISH', 'signal': 'IB entirely above overnight POC',
                'detail': (f'Entire IB sits above overnight POC ({on_poc:.2f}). '
                           f'Day-session buyers winning value. Pull back to ON POC ({on_poc:.2f}) '
                           f'is the first buy opportunity against longs.')})
            key_levels.append({'level': on_poc, 'label': 'ON POC → Buy zone', 'role': 'support', 'color': 'cyan'})
        elif ib_high < on_poc - tick:
            # Entire IB below ON POC
            bias_score -= 1
            signals.append({'type': 'BEARISH', 'signal': 'IB entirely below overnight POC',
                'detail': (f'Entire IB sits below overnight POC ({on_poc:.2f}). '
                           f'Day-session sellers winning value. Rally to ON POC ({on_poc:.2f}) '
                           f'is the first short opportunity.')})
            key_levels.append({'level': on_poc, 'label': 'ON POC — resistance / short entry', 'role': 'resistance', 'color': 'cyan'})
        elif b_close is not None:
            # IB range crosses ON POC — use B period close to determine structure
            if b_close > on_poc + straddle_thresh:
                # B closed well above ON POC → ON POC was offered, tested, and refused as support
                bias_score += 1
                pts_above = round(b_close - on_poc, 2)
                signals.append({'type': 'BULLISH', 'signal': 'ON POC held as support during IB',
                    'detail': (f'IB briefly tested overnight POC ({on_poc:.2f}) but B period '
                               f'closed at {b_close:.2f} — {pts_above} pts above. '
                               f'ON POC was offered and refused; buyers defended it. '
                               f'ON POC is confirmed as a buy-zone floor beneath the IB.')})
                key_levels.append({'level': on_poc, 'label': 'ON POC → Support / Buy zone', 'role': 'support', 'color': 'cyan'})
            elif b_close < on_poc - straddle_thresh:
                # B closed well below ON POC → ON POC acted as a ceiling that sellers defended
                bias_score -= 1
                pts_below = round(on_poc - b_close, 2)
                signals.append({'type': 'BEARISH', 'signal': 'ON POC acted as resistance during IB',
                    'detail': (f'IB briefly tested overnight POC ({on_poc:.2f}) from below but B period '
                               f'closed at {b_close:.2f} — {pts_below} pts below. '
                               f'Sellers defended ON POC as a ceiling. '
                               f'ON POC is confirmed as a sell-zone resistance above the IB.')})
                key_levels.append({'level': on_poc, 'label': 'ON POC — resistance / short entry', 'role': 'resistance', 'color': 'cyan'})
            else:
                # B close within 3 ticks of ON POC — genuine straddle, neither side committed
                signals.append({'type': 'NEUTRAL', 'signal': 'IB straddles overnight POC',
                    'detail': (f'Overnight POC ({on_poc:.2f}) sits inside the IB and B period '
                               f'closed at {b_close:.2f} — within {straddle_thresh:.2f} pts. '
                               f'Neither side has won the value argument. '
                               f'Wait for C period to close decisively above or below ON POC.')})
        else:
            # No B period close available (only A period complete)
            signals.append({'type': 'NEUTRAL', 'signal': 'IB straddles overnight POC',
                'detail': (f'Overnight POC ({on_poc:.2f}) sits inside the IB — neither side winning value. '
                           f'Wait for a decisive close above or below ON POC before committing.')})

    # ── 3. Overnight inventory alignment ─────────────────────────────────────
    ON_LETTERS = list('abcdefghijklmnopqrstuvwxyz') + ['1', '2', '3', '4', '5']
    first_ltr = next((l for l in ON_LETTERS if l in on_pr), None)
    last_ltr  = next((l for l in reversed(ON_LETTERS) if l in on_pr), None)
    if first_ltr and last_ltr and first_ltr != last_ltr:
        fr = on_pr[first_ltr]; lr = on_pr[last_ltr]
        on_mid_open  = (fr['high'] + fr['low']) / 2
        on_mid_close = (lr['high'] + lr['low']) / 2
        on_bullish   = on_mid_close > on_mid_open
        if on_bullish and on_poc and ib_low > on_poc:
            bias_score += 1
            signals.append({'type': 'BULLISH', 'signal': 'Overnight inventory aligned bullish',
                'detail': (f'Overnight trended higher AND IB above ON POC ({on_poc:.2f}). '
                           f'Overnight longs are "right" — do not fade early rallies. '
                           f'Hold longs until the market gives a rotational signal (3 consecutive lower highs).')})
        elif not on_bullish and on_poc and ib_high < on_poc:
            bias_score -= 1
            signals.append({'type': 'BEARISH', 'signal': 'Overnight inventory aligned bearish',
                'detail': (f'Overnight trended lower AND IB below ON POC ({on_poc:.2f}). '
                           f'Overnight shorts are "right" — do not buy dips early. '
                           f'Hold shorts until the market gives a rotational signal.')})
        elif on_bullish and on_poc and ib_high < on_poc:
            signals.append({'type': 'CAUTION', 'signal': 'Inventory misalignment — potential forced unwind',
                'detail': (f'Overnight trended higher but IB is BELOW ON POC ({on_poc:.2f}). '
                           f'Overnight longs are now wrong — expect liquidation pressure. '
                           f'Bearish until IB reclaims ON POC.')})
        elif not on_bullish and on_poc and ib_low > on_poc:
            signals.append({'type': 'CAUTION', 'signal': 'Inventory misalignment — potential short squeeze',
                'detail': (f'Overnight trended lower but IB is ABOVE ON POC ({on_poc:.2f}). '
                           f'Overnight shorts are now wrong — expect short-covering. '
                           f'Bullish until IB fails ON POC.')})

    # ── 4. Extension targets (prior RTH levels) ───────────────────────────────
    if bias_score > 0 and p_vah is not None and ib_high < p_vah:
        key_levels.append({'level': p_vah, 'label': 'Prior VAH → Extension target', 'role': 'target_up', 'color': 'purple'})
    if bias_score < 0 and p_val is not None and ib_low > p_val:
        key_levels.append({'level': p_val, 'label': 'Prior VAL → Extension target', 'role': 'target_down', 'color': 'purple'})
    if p_poc is not None:
        key_levels.append({'level': p_poc, 'label': 'Prior RTH POC', 'role': 'pivot', 'color': 'purple'})

    # ── 5. IB range context ───────────────────────────────────────────────────
    p_range = (p_high - p_low) if p_high and p_low else None
    day_context = ''
    if ib_range and p_range:
        ratio = ib_range / p_range
        if ratio < 0.25:
            day_context = (f'Narrow IB ({ib_range:.2f} = {ratio*100:.0f}% of prior range) — '
                           f'coiled spring. Trend Day probability elevated. If/when breakout occurs, '
                           f'size up and ride it. Failure to break = fade back to midpoint.')
        elif ratio > 0.55:
            day_context = (f'Wide IB ({ib_range:.2f} = {ratio*100:.0f}% of prior range) — '
                           f'both sides accepted broad value. Normal or Neutral day likely. '
                           f'Rotate within IB; extension of 1–1.5× IB is the likely daily range.')
        else:
            day_context = (f'Average IB ({ib_range:.2f} = {ratio*100:.0f}% of prior range) — '
                           f'Normal Variation most likely. One-sided extension expected. '
                           f'Bias determined by IB location vs overnight POC.')

    # ── 6. Trade plan ──────────────────────────────────────────────────────────
    onh_s = f'{on_high:.2f}' if on_high is not None else 'ONH'
    onl_s = f'{on_low:.2f}'  if on_low  is not None else 'ONL'
    poc_s = f'{on_poc:.2f}'  if on_poc  is not None else 'ON POC'

    if bias_score >= 2:
        bias = 'BULLISH'; bias_label = 'Bullish'
        if onh_probe_rejected:
            # Probe above ONH was rejected — ONH is still resistance, not support yet.
            # C period close above ONH is required to confirm.
            trade_plan = (f'OA session — probe above ONH ({onh_s}) was rejected in B. '
                          f'Watch C: close above ONH confirms bullish bias — then buy pullbacks to ONH. '
                          f'Until C confirms: buy ON VAL ({val_s}), sell ON VAH ({vah_s}). '
                          f'ON POC ({poc_s}) is the key support floor.')
        else:
            trade_plan = f'Buy pullbacks to ONH ({onh_s})'
            if on_poc and ib_low > on_poc:
                trade_plan += f' and ON POC ({poc_s}). '
            else:
                trade_plan += '. '
            on_high_val = float(onh_s) if onh_s != 'ONH' else None
            if p_vah and on_high_val and p_vah > on_high_val:
                trade_plan += f'Target Prior VAH {p_vah:.2f}. Do not short against the trend.'
            else:
                trade_plan += 'Trail stops on new highs. Do not short against the trend.'
    elif bias_score <= -2:
        bias = 'BEARISH'; bias_label = 'Bearish'
        if onl_probe_rejected:
            # Probe below ONL was rejected — ONL is still support, not resistance yet.
            trade_plan = (f'OA session — probe below ONL ({onl_s}) was rejected in B. '
                          f'Watch C: close below ONL confirms bearish bias — then sell rallies to ONL. '
                          f'Until C confirms: sell ON VAH ({vah_s}), buy ON VAL ({val_s}). '
                          f'ON POC ({poc_s}) is the key resistance ceiling.')
        else:
            trade_plan = f'Sell rallies to ONL ({onl_s})'
            if on_poc and ib_high < on_poc:
                trade_plan += f' and ON POC ({poc_s}). '
            else:
                trade_plan += '. '
            on_low_val = float(onl_s) if onl_s != 'ONL' else None
            if p_val and on_low_val and p_val < on_low_val:
                trade_plan += f'Target Prior VAL {p_val:.2f}. Do not buy against the trend.'
            else:
                trade_plan += 'Trail stops on new lows. Do not buy against the trend.'
    elif bias_score == 1:
        bias = 'BULLISH_LEAN'; bias_label = 'Bullish Lean'
        if open_inside_on and ib_above_onh:
            trade_plan = (f'OA open with bullish IB probe above ONH ({onh_s}). '
                          f'If price accepts and holds above ONH, buy pullbacks targeting '
                          f'overnight VAH ({vah_s}) and beyond. '
                          f'If ONH fails, two-sided OA: sell overnight VAH ({vah_s}), '
                          f'buy overnight VAL ({val_s}).')
        else:
            trade_plan = (f'Slight bullish edge — await confirmation. '
                          f'Buy IB Low ({ib_low:.2f}) on tests; look for failure to hold below as invalidation.')
    elif bias_score == -1:
        bias = 'BEARISH_LEAN'; bias_label = 'Bearish Lean'
        if open_inside_on and ib_below_onl:
            trade_plan = (f'OA open with bearish IB probe below ONL ({onl_s}). '
                          f'If price accepts and holds below ONL, sell rallies targeting '
                          f'overnight VAL ({val_s}) and beyond. '
                          f'If ONL holds, two-sided OA: buy overnight VAL ({val_s}), '
                          f'sell overnight VAH ({vah_s}).')
        else:
            trade_plan = (f'Slight bearish edge — await confirmation. '
                          f'Sell IB High ({ib_high:.2f}) on tests; look for failure to hold above as invalidation.')
    else:
        bias = 'NEUTRAL'; bias_label = 'Neutral'
        if open_inside_on and ib_in_on:
            trade_plan = (f'Classic OA day — overnight traders in control. '
                          f'Buy overnight VAL ({val_s}), sell overnight VAH ({vah_s}). '
                          f'Watch for decisive break of the overnight range to set directional bias.')
        else:
            trade_plan = (f'No directional edge. Buy IB Low ({ib_low:.2f}), sell IB High ({ib_high:.2f}). '
                          f'Late-session close outside IB sets tomorrow\'s opening bias.')

    key_levels_out = sorted(key_levels, key=lambda x: x['level'], reverse=True)

    return {
        'ready':       True,
        'bias':        bias,
        'bias_label':  bias_label,
        'ib_score':    bias_score,    # raw numeric score for live-adjustment use
        'signals':     signals,
        'key_levels':  key_levels_out,
        'day_context': day_context,
        'trade_plan':  trade_plan,
    }


def _zone_to_live_adj(zone: str, ib_score: int,
                      confirmed_dir: int = 0) -> int:
    """Convert a live-read zone into a signed score adjustment.

    The adjustment is always applied in the direction that either
    confirms or contradicts the original IB thesis:

      Bullish IB (ib_score > 0):           Bearish IB (ib_score < 0):
        CONFIRMED  → +2  (accelerating)      CONFIRMED  → −2
        INTACT     →  0  (holding)           INTACT     →  0
        WEAKENING  → −1  (fading)            WEAKENING  → +1
        CRITICAL   → −2  (under stress)      CRITICAL   → +2
        INVALIDATED→ −3  (flip)              INVALIDATED→ +3

      Neutral IB (ib_score == 0) — zone carries its own direction:
        CONFIRMED above ON range → +2   (confirmed_dir = +1)
        CONFIRMED below ON range → −2   (confirmed_dir = −1)
        INTACT                  →  0
        WEAKENING               → −1
        CRITICAL                → −2
        INVALIDATED             → −3

    For neutral IB the caller MUST supply confirmed_dir (+1 or -1) when
    the zone is CONFIRMED so the sign is resolved correctly.  Without it
    the function would default direction=+1 (ib_score 0 >= 0) and always
    return +2, incorrectly labelling a bearish breakdown as Bullish.
    """
    _MAP = {
        'CONFIRMED':   2,
        'INTACT':      0,
        'WEAKENING':  -1,
        'CRITICAL':   -2,
        'INVALIDATED':-3,
        'IB_BUILDING': 0,
        'BUILDING':    0,
    }
    raw = _MAP.get(zone, 0)

    # Neutral IB + CONFIRMED: direction comes from price vs overnight range,
    # not from the IB score sign (which is 0 and would wrongly default to +1).
    if ib_score == 0 and zone == 'CONFIRMED' and confirmed_dir != 0:
        return confirmed_dir * raw

    direction = 1 if ib_score >= 0 else -1
    return direction * raw


def _score_to_bias(score: int) -> tuple[str, str]:
    """Convert a numeric score to (bias, label) strings."""
    if   score >=  4: return 'BULLISH',      'Strong Bullish'
    elif score ==  3: return 'BULLISH',      'Bullish'
    elif score ==  2: return 'BULLISH',      'Bullish'
    elif score ==  1: return 'BULLISH_LEAN', 'Bullish Lean'
    elif score ==  0: return 'NEUTRAL',      'Neutral'
    elif score == -1: return 'BEARISH_LEAN', 'Bearish Lean'
    elif score == -2: return 'BEARISH',      'Bearish'
    elif score == -3: return 'BEARISH',      'Bearish'
    else:             return 'BEARISH',      'Strong Bearish'


def _zone_for_close(close: float, bias: str, on_high, on_low, on_poc,
                     ib_high, ib_low, straddle_t: float, tick: float) -> str:
    """Return zone name for a single period close given the IB bias.

    Zones (bullish): CONFIRMED > INTACT > WEAKENING > CRITICAL > INVALIDATED
    Zones (bearish): mirror of bullish
    Zones (neutral): CONFIRMED (outside ON range) or INTACT (inside ON range)
    """
    if bias in ('BULLISH', 'BULLISH_LEAN'):
        if ib_high and close > ib_high + tick:
            return 'CONFIRMED'
        if on_high and close >= on_high - straddle_t:
            return 'INTACT'
        if on_poc and close > on_poc + straddle_t:
            return 'WEAKENING'
        if on_poc and close >= on_poc - straddle_t:
            return 'CRITICAL'
        return 'INVALIDATED'
    elif bias in ('BEARISH', 'BEARISH_LEAN'):
        if ib_low and close < ib_low - tick:
            return 'CONFIRMED'
        if on_low and close <= on_low + straddle_t:
            return 'INTACT'
        if on_poc and close < on_poc - straddle_t:
            return 'WEAKENING'
        if on_poc and close <= on_poc + straddle_t:
            return 'CRITICAL'
        return 'INVALIDATED'
    else:
        # NEUTRAL / OA
        if on_high and close > on_high + straddle_t:
            return 'CONFIRMED'
        if on_low and close < on_low - straddle_t:
            return 'CONFIRMED'
        return 'INTACT'


# Zone severity order — higher index = worse/further from original signal
_ZONE_SEVERITY = ['CONFIRMED', 'INTACT', 'WEAKENING', 'CRITICAL', 'INVALIDATED']

def _is_downgrade(prev_zone: str, curr_zone: str) -> bool:
    """Return True if curr_zone is a deterioration vs prev_zone."""
    try:
        return _ZONE_SEVERITY.index(curr_zone) > _ZONE_SEVERITY.index(prev_zone)
    except ValueError:
        return False


def _build_live_trade_plan(
    zone: str, current_bias: str,
    on_high, on_low, on_poc, ib_high, ib_low,
) -> str | None:
    """Return a live trade plan based on the current zone + live-adjusted bias.

    Only fires once the IB is complete (zone is a real zone, not BUILDING/IB_BUILDING).
    The plan updates every period and always reflects the current market truth,
    overriding the frozen IB hypothesis when the signal has flipped.
    """
    onh = f'{on_high:.2f}' if on_high is not None else 'ONH'
    onl = f'{on_low:.2f}'  if on_low  is not None else 'ONL'
    poc = f'{on_poc:.2f}'  if on_poc  is not None else 'ON POC'
    ibh = f'{ib_high:.2f}' if ib_high is not None else 'IB High'
    ibl = f'{ib_low:.2f}'  if ib_low  is not None else 'IB Low'

    bullish = current_bias in ('BULLISH', 'BULLISH_LEAN')
    bearish = current_bias in ('BEARISH', 'BEARISH_LEAN')

    if zone == 'CONFIRMED':
        if bullish:
            return (f'Ride longs. Trail stop below ONH ({onh}). '
                    f'Do not fade — buyers extending above IB High ({ibh}).')
        if bearish:
            return (f'Ride shorts. Trail stop above ONL ({onl}). '
                    f'Do not fade — sellers extending below IB Low ({ibl}).')

    elif zone == 'INTACT':
        if bullish:
            return (f'Buy pullbacks to ONH ({onh}). Stop below ON POC ({poc}). '
                    f'Target IB High ({ibh}).')
        if bearish:
            return (f'Sell rallies to ONL ({onl}). Stop above ON POC ({poc}). '
                    f'Target IB Low ({ibl}).')

    elif zone == 'WEAKENING':
        if bullish:
            return (f'Reduce longs 50%. No new entries. '
                    f'ON POC ({poc}) is next key support — close below signals full exit.')
        if bearish:
            return (f'Cover shorts 50%. No new entries. '
                    f'ON POC ({poc}) is next key resistance — close above signals full cover.')

    elif zone == 'CRITICAL':
        if bullish:
            return (f'Exit all longs. ON POC ({poc}) is last defence. '
                    f'No new positions until a period closes above {poc}.')
        if bearish:
            return (f'Cover all shorts. ON POC ({poc}) is last defence. '
                    f'No new positions until a period closes below {poc}.')

    elif zone == 'INVALIDATED':
        if bullish:
            # Original was bearish, flipped — buy side now
            return (f'Signal flipped BULLISH. Cover shorts. '
                    f'Buy dips to ON POC ({poc}, now support). '
                    f'Stop below ONL ({onl}). Target ONH ({onh}).')
        if bearish:
            # Original was bullish, flipped — sell side now
            return (f'Signal flipped BEARISH. Exit all longs. '
                    f'Sell rallies to ON POC ({poc}, now resistance). '
                    f'Stop above ONH ({onh}). Target ONL ({onl}).')

    return None


def _evaluate_live_read(session_prof: dict, ib_signals: dict, overnight: dict,
                         now_et, tick: float) -> dict:
    """Evaluate the live market read based on the most recently CLOSED TPO period.

    Updates on every API refresh.  Reads each new letter against key IB reference
    levels (ONH, ON POC, ONL) and returns a zone status + narrative + guidance.

    Zones for bullish IB signal (mirror for bearish):
      CONFIRMED  — period closed above IB High (extension in progress)
      INTACT     — period closed above ONH (excess holding)
      WEAKENING  — period closed below ONH but above ON POC (OA two-sided active)
      CRITICAL   — period closed at ON POC ± 3 ticks (last defence)
      INVALIDATED— period closed below ON POC (signal proven wrong)

    Consecutive confirmation rule: downgrade zones (e.g., INTACT → WEAKENING)
    require TWO consecutive period closes in the new zone.  A single-period dip
    keeps the prior status while flagging a "⚠ First warning" in the narrative.
    Upgrades (improving zones) and CONFIRMED always take effect immediately.
    """
    RTH_START = 9 * 60 + 30
    t_min     = now_et.hour * 60 + now_et.minute
    pr        = session_prof.get('period_ranges', {})

    on_high   = overnight.get('high')
    on_low    = overnight.get('low')
    on_poc    = overnight.get('poc')
    on_vah    = overnight.get('vah')
    on_val    = overnight.get('val')
    ib_high   = session_prof.get('ib_high')
    ib_low    = session_prof.get('ib_low')

    on_high_s = f'{on_high:.2f}' if on_high is not None else 'n/a'
    on_low_s  = f'{on_low:.2f}'  if on_low  is not None else 'n/a'
    on_poc_s  = f'{on_poc:.2f}'  if on_poc  is not None else 'n/a'
    on_vah_s  = f'{on_vah:.2f}'  if on_vah  is not None else 'n/a'
    on_val_s  = f'{on_val:.2f}'  if on_val  is not None else 'n/a'

    _building = {
        'active': False, 'status': 'BUILDING',
        'last_period': None, 'last_close': None,
        'current_read': 'RTH has not started.',
        'live_guidance': f'Overnight range: {on_low_s} – {on_high_s}. ON POC: {on_poc_s}.',
        'watch_level': None,
    }

    # ── Determine last COMPLETED period ──────────────────────────────────────
    if t_min >= 16 * 60:
        last_complete_idx = 12          # M period complete (session ended)
    elif t_min >= RTH_START:
        last_complete_idx = (t_min - RTH_START) // 30 - 1
    else:
        return _building                # pre-RTH

    if last_complete_idx < 0:
        # A period still building (9:30–10:00 ET) — no period has closed yet
        a_last = pr.get('A', {}).get('close')   # last 1-min bar close, NOT a period close
        if a_last is None:
            return _building
        if on_high and a_last > on_high + tick:
            read = (f'A period building — last print {a_last:.2f}, above ONH ({on_high_s}). '
                    f'Buyers probing above overnight range. '
                    f'A closes at 10:00 AM ET.')
        elif on_low and a_last < on_low - tick:
            read = (f'A period building — last print {a_last:.2f}, below ONL ({on_low_s}). '
                    f'Sellers probing below overnight range. '
                    f'A closes at 10:00 AM ET.')
        elif on_poc and a_last > on_poc + tick:
            read = (f'A period building — last print {a_last:.2f}, above ON POC ({on_poc_s}). '
                    f'Buyers holding above overnight value. '
                    f'A closes at 10:00 AM ET.')
        elif on_poc and a_last < on_poc - tick:
            read = (f'A period building — last print {a_last:.2f}, below ON POC ({on_poc_s}). '
                    f'Sellers below overnight value. '
                    f'A closes at 10:00 AM ET.')
        else:
            read = (f'A period building — last print {a_last:.2f}, near ON POC ({on_poc_s}). '
                    f'No directional edge yet. A closes at 10:00 AM ET.')
        return {
            'active': True, 'status': 'IB_BUILDING',
            'last_period': 'A', 'last_close': a_last,
            'current_read': read,
            'live_guidance': 'A period in progress (9:30–10:00 ET) — no periods closed yet.',
            'watch_level': {'price': on_high, 'label': 'ONH', 'significance': 'A close above = bullish probe'} if on_high else None,
        }

    last_letter = chr(ord('A') + min(last_complete_idx, 12))
    last_data   = pr.get(last_letter, {})
    last_close  = last_data.get('close')

    if last_close is None:
        return _building

    # ── IB just complete (last = B) — read IB bias as the baseline ───────────
    # Gate on CLOCK TIME, not just period count — _build_rth_tpo_profile
    # includes developing B bars in period_ranges before B closes, so
    # ib_signals.ready can be True as early as 10:00 AM. Enforce 10:30 AM ET.
    IB_COMPLETE_MIN = RTH_START + 60   # 10:30 AM ET (A + B = 2 × 30min)
    ib_clock_ready  = t_min >= IB_COMPLETE_MIN
    ib_ready        = ib_signals.get('ready', False) and ib_clock_ready

    if not ib_ready:
        # Show developing B period context while it's building (10:00–10:30 AM)
        b_data  = session_prof.get('period_ranges', {}).get('B', {})
        b_hi    = b_data.get('high')
        b_lo    = b_data.get('low')
        b_last  = b_data.get('close')   # most recent close in developing B

        if last_complete_idx == 0 and b_last is not None:
            # A complete, B developing — show live B vs key levels
            vs_onl = f'{b_last - on_low:.2f} pts above ONL ({on_low_s})' if on_low and b_last > on_low else \
                     f'{on_low - b_last:.2f} pts BELOW ONL ({on_low_s})' if on_low else ''
            vs_onh = f'{on_high - b_last:.2f} pts below ONH ({on_high_s})' if on_high and b_last < on_high else \
                     f'{b_last - on_high:.2f} pts ABOVE ONH ({on_high_s})' if on_high else ''
            read = (f'A closed at {last_close:.2f} — B period building '
                    f'(Hi: {b_hi:.2f}  Lo: {b_lo:.2f}  Last: {b_last:.2f}). '
                    f'{vs_onl}. {vs_onh}. IB completes at 10:30 AM ET.')
        else:
            read = (f'{last_letter} period closed at {last_close:.2f}. '
                    f'IB completes when B period closes at 10:30 AM ET.')

        return {
            'active': True, 'status': 'IB_BUILDING',
            'last_period': last_letter, 'last_close': last_close,
            'current_read': read,
            'live_guidance': f'Wait for B close (10:30 AM ET) — IB will set the directional bias.',
            'watch_level': {'price': on_low, 'label': 'ONL', 'significance': 'B close below = bearish IB'} if on_low else None,
        }

    bias        = ib_signals.get('bias', 'NEUTRAL')
    ib_score_raw = ib_signals.get('ib_score', 0)
    straddle_t  = 3 * tick

    # Helper: period label with human-readable time
    def _plabel(letter: str) -> str:
        idx       = ord(letter) - ord('A')
        t_s       = 570 + idx * 30        # minutes since midnight (A=9:30)
        t_e       = t_s + 30
        h_s, m_s  = divmod(t_s, 60)
        h_e, m_e  = divmod(t_e, 60)
        return f'{letter}  ({h_s}:{m_s:02d}–{h_e}:{m_e:02d})'

    period_label = _plabel(last_letter)

    # ── Zone classification for last & previous period ────────────────────────
    curr_zone = _zone_for_close(last_close, bias, on_high, on_low, on_poc,
                                 ib_high, ib_low, straddle_t, tick)

    prev_zone: str | None = None
    prev_close: float | None = None
    if last_complete_idx > 0:           # there is a previous period
        prev_letter = chr(ord('A') + last_complete_idx - 1)
        prev_data   = pr.get(prev_letter, {})
        prev_close  = prev_data.get('close')
        if prev_close is not None:
            prev_zone = _zone_for_close(prev_close, bias, on_high, on_low, on_poc,
                                         ib_high, ib_low, straddle_t, tick)

    # ── Consecutive-period confirmation rule ──────────────────────────────────
    # Downgrades require two consecutive closes in the new zone.
    # First-period dip (or very first period of the session): warn in narrative.
    # Upgrades and CONFIRMED are immediate.
    first_warning = False
    if prev_zone is None:
        # Very first period close of the session — can never be "two consecutive"
        first_warning = True
        effective_zone = curr_zone
    elif (curr_zone != 'CONFIRMED'
            and _is_downgrade(prev_zone, curr_zone)):
        first_warning = True   # single period dip — hold prior status for badge
        effective_zone = prev_zone
    else:
        effective_zone = curr_zone

    status   = effective_zone
    read     = ''
    guidance = ''
    watch    = None

    if bias in ('BULLISH', 'BULLISH_LEAN'):
        if on_high is None or on_poc is None:
            return _building

        if effective_zone == 'CONFIRMED':
            ib_ref = f'{ib_high:.2f}' if ib_high else on_high_s
            if first_warning and curr_zone != 'CONFIRMED':
                pts_below = round((ib_high or on_high) - last_close, 2)
                read     = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_below} pts below IB High ({ib_ref}) — '
                            f'first pullback from extension. One period is noise; CONFIRMED badge held. '
                            f'Watch next close: above IB High restores full extension, below ONH downgrades.')
                guidance = (f'First pullback from the IB High extension. '
                            f'Buyers still in control — ONH ({on_high_s}) is the key floor. '
                            f'One period above IB High restores CONFIRMED.')
                watch    = {'price': ib_high or on_high, 'label': 'IB High — watching', 'significance': 'close above → CONFIRMED restored'}
            else:
                excess   = round(last_close - (ib_high or on_high), 2)
                read     = (f'{period_label} closed at {last_close:.2f}, extending {excess} pts above '
                            f'IB High ({ib_ref}). Buyers accelerating — trend day developing.')
                guidance = (f'Buyers extended above IB High — one-timeframe control. '
                            f'Trend day developing. ONH ({on_high_s}) is the trailing reference level.')
                watch    = {'price': on_high, 'label': 'ONH — trail stop', 'significance': 'close below → reduce position'}

        elif effective_zone == 'INTACT':
            pts = round(last_close - on_high, 2)
            if first_warning and prev_zone is not None:
                pts_below = round(on_high - last_close, 2)
                read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_below} pts below ONH ({on_high_s}) — '
                        f'first dip below support. One period is noise; watching for confirmation. '
                        f'Previous period held above ONH.')
                guidance = (f'First dip below ONH ({on_high_s}) — INTACT badge held pending second close. '
                            f'One period is noise. A second close below ONH confirms the zone is weakening.')
                watch = {'price': on_high, 'label': 'ONH — watching', 'significance': 'second close below → weakening'}
            else:
                direction = 'above' if pts > 0 else 'below' if pts < 0 else 'at'
                read = (f'{period_label} closed at {last_close:.2f}, '
                        f'{direction} ONH ({on_high_s}) '
                        f'by {abs(pts)} pts. IB bullish excess intact — '
                        f'buyers defending ONH as support.')
                guidance = (f'IB bullish excess intact. Buyers holding above ONH ({on_high_s}). '
                            f'Zone is healthy — ON POC ({on_poc_s}) is the key support below.')
                watch = {'price': on_high, 'label': 'ONH', 'significance': 'close below → weakening'}

        elif effective_zone == 'WEAKENING':
            pts_gap = round(on_high - last_close, 2)
            pts_to_poc = round(last_close - on_poc, 2)
            if first_warning and prev_zone is not None:
                if last_close < on_poc - straddle_t:
                    # Jumped past CRITICAL straight into INVALIDATED territory in one bar.
                    pts_below_poc = round(on_poc - last_close, 2)
                    read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_below_poc} pts below ON POC ({on_poc_s}) — '
                            f'first close below the key support. Signal approaching invalidation. '
                            f'A second close below ON POC fully invalidates the bullish setup.')
                    guidance = (f'First close below ON POC ({on_poc_s}) — one period is noise, but buyers must respond. '
                                f'A second close below confirms the bullish thesis is broken; ON POC becomes resistance. '
                                f'Reduce longs; no new entries until price recovers back above ON POC.')
                    watch = {'price': on_poc, 'label': 'ON POC — first breach', 'significance': 'second close below → fully invalidated'}
                else:
                    read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_gap} pts below ONH ({on_high_s}) — '
                            f'first test of ONH as resistance. One period is noise. '
                            f'ON POC ({on_poc_s}) is the next key level to watch.')
                    guidance = (f'First period below ONH — one period is noise. '
                                f'If next period also closes below ONH, OA two-sided behaviour is confirmed. '
                                f'ON POC ({on_poc_s}) is the critical pivot below.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close below → invalidated'}
            else:
                _wk_range = on_high - on_poc
                _pct = (on_high - last_close) / _wk_range if _wk_range > 0 else 0
                if _pct >= 0.7:
                    read = (f'{period_label} closed at {last_close:.2f}, {abs(pts_to_poc)} pts above ON POC ({on_poc_s}). '
                            f'Approaching the invalidation level — bulls must defend ON POC.')
                    guidance = (f'Price pressing ON POC ({on_poc_s}) from above — the key support for the bullish signal. '
                                f'A close below ON POC confirms the setup has failed. '
                                f'No new longs here; manage existing position size.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close below → invalidated'}
                else:
                    read = (f'{period_label} closed at {last_close:.2f}, {pts_gap} pts below ONH ({on_high_s}). '
                            f'Two consecutive closes below ONH confirmed — ONH failed as support. '
                            f'Market returned inside overnight range; OA two-sided behaviour active.')
                    guidance = (f'ONH ({on_high_s}) has failed as support — two consecutive closes below. '
                                f'Market is back inside the overnight range. '
                                f'Neither side has directional control until ONH is reclaimed or ON POC breaks.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close below → invalidated'}

        elif effective_zone == 'CRITICAL':
            if first_warning and prev_zone is not None:
                read = (f'⚠ {period_label} closed at {last_close:.2f} — first touch of ON POC ({on_poc_s}). '
                        f'Buyers must defend this level immediately. '
                        f'A second close at or below ON POC confirms breakdown.')
                guidance = (f'First touch of ON POC ({on_poc_s}) — last line of defence for the bullish signal. '
                            f'Buyers must hold here. A second close below confirms full signal failure.')
                watch = {'price': on_poc, 'label': 'ON POC — first touch', 'significance': 'second close below → invalidated'}
            else:
                read = (f'{period_label} closed at {last_close:.2f} — at ON POC ({on_poc_s}). '
                        f'Two consecutive closes at last support for the bullish IB signal. '
                        f'A period close below ON POC invalidates the setup completely.')
                guidance = (f'Two consecutive closes at ON POC ({on_poc_s}) — signal at last defence. '
                            f'Bullish thesis requires an immediate close above to survive.')
                watch = {'price': on_poc, 'label': 'ON POC — critical', 'significance': 'close below → invalidated'}

        else:  # INVALIDATED
            pts_below = round(on_poc - last_close, 2)
            if first_warning:
                read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_below} pts below ON POC ({on_poc_s}) — '
                        f'first close below the last line of defence. '
                        f'One more period here confirms full invalidation.')
                guidance = (f'First close below ON POC ({on_poc_s}) — approaching full invalidation. '
                            f'One more period below confirms the bullish thesis is broken.')
                watch = {'price': on_poc, 'label': 'ON POC — first breach', 'significance': 'second close below → fully invalidated'}
            else:
                read = (f'{period_label} closed at {last_close:.2f}, {pts_below} pts below ON POC ({on_poc_s}). '
                        f'Bullish IB signal invalidated — two consecutive closes below ON POC. '
                        f'ON POC is now resistance; ONL ({on_low_s}) is the next downside target.')
                guidance = (f'Bullish signal invalidated — two closes below ON POC. '
                            f'ON POC ({on_poc_s}) is now resistance; overnight sellers in control. '
                            f'Next support: ONL ({on_low_s}).')
                watch = {'price': on_low, 'label': 'ONL', 'significance': 'close below → breakdown'}

    elif bias in ('BEARISH', 'BEARISH_LEAN'):
        if on_low is None or on_poc is None:
            return _building

        if effective_zone == 'CONFIRMED':
            ib_ref = f'{ib_low:.2f}' if ib_low else on_low_s
            if first_warning and curr_zone != 'CONFIRMED':
                pts_above = round(last_close - (ib_low or on_low), 2)
                read     = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_above} pts above IB Low ({ib_ref}) — '
                            f'first pullback from extension. One period is noise; CONFIRMED badge held. '
                            f'Watch next close: below IB Low restores full extension, above ONL downgrades.')
                guidance = (f'First pullback from the IB Low extension. '
                            f'Sellers still in control — ONL ({on_low_s}) is the key ceiling. '
                            f'One period below IB Low restores CONFIRMED.')
                watch    = {'price': ib_low or on_low, 'label': 'IB Low — watching', 'significance': 'close below → CONFIRMED restored'}
            else:
                excess   = round((ib_low or on_low) - last_close, 2)
                read     = (f'{period_label} closed at {last_close:.2f}, extending {excess} pts below '
                            f'IB Low ({ib_ref}). Sellers accelerating — trend day developing.')
                guidance = (f'Sellers extended below IB Low — one-timeframe control. '
                            f'Trend day developing. ONL ({on_low_s}) is the trailing reference level.')
                watch    = {'price': on_low, 'label': 'ONL — trail stop', 'significance': 'close above → reduce position'}

        elif effective_zone == 'INTACT':
            pts = round(on_low - last_close, 2)
            if first_warning and prev_zone is not None:
                pts_above = round(last_close - on_low, 2)
                read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_above} pts above ONL ({on_low_s}) — '
                        f'first recovery above resistance. One period is noise; watching for confirmation. '
                        f'Previous period held below ONL.')
                guidance = (f'First close above ONL ({on_low_s}) — INTACT badge held pending second close. '
                            f'One period is noise. A second close above ONL confirms the zone is weakening.')
                watch = {'price': on_low, 'label': 'ONL — watching', 'significance': 'second close above → weakening'}
            else:
                direction = 'below' if pts > 0 else 'above' if pts < 0 else 'at'
                read = (f'{period_label} closed at {last_close:.2f}, '
                        f'{direction} ONL ({on_low_s}) by {abs(pts)} pts. '
                        f'IB bearish excess intact — sellers defending ONL as resistance.')
                guidance = (f'IB bearish excess intact. Sellers holding below ONL ({on_low_s}). '
                            f'Zone is healthy — ON POC ({on_poc_s}) is the key resistance above.')
                watch = {'price': on_low, 'label': 'ONL', 'significance': 'close above → weakening'}

        elif effective_zone == 'WEAKENING':
            pts_gap = round(last_close - on_low, 2)
            pts_to_poc = round(on_poc - last_close, 2)
            if first_warning and prev_zone is not None:
                if last_close > on_poc + straddle_t:
                    # Jumped past CRITICAL straight into INVALIDATED territory in one bar.
                    pts_above_poc = round(last_close - on_poc, 2)
                    read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_above_poc} pts above ON POC ({on_poc_s}) — '
                            f'first close above the key resistance. Signal approaching invalidation. '
                            f'A second close above ON POC fully invalidates the bearish setup.')
                    guidance = (f'First close above ON POC ({on_poc_s}) — one period is noise, but sellers must respond. '
                                f'A second close above confirms the bearish thesis is broken; ON POC becomes support. '
                                f'Cover remaining shorts; no new entries until price falls back below ON POC.')
                    watch = {'price': on_poc, 'label': 'ON POC — first breach', 'significance': 'second close above → fully invalidated'}
                else:
                    read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_gap} pts above ONL ({on_low_s}) — '
                            f'first test of ONL as support. One period is noise. '
                            f'ON POC ({on_poc_s}) is the next key level to watch.')
                    guidance = (f'First period above ONL — one period is noise. '
                                f'If next period also closes above ONL, OA two-sided behaviour is confirmed. '
                                f'ON POC ({on_poc_s}) is the critical pivot above.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close above → invalidated'}
            else:
                _wk_range = on_poc - on_low
                _pct = (last_close - on_low) / _wk_range if _wk_range > 0 else 0
                if _pct >= 0.7:
                    read = (f'{period_label} closed at {last_close:.2f}, {pts_to_poc} pts below ON POC ({on_poc_s}). '
                            f'Approaching the invalidation level — sellers must hold ON POC as resistance.')
                    guidance = (f'Price pressing ON POC ({on_poc_s}) from below — the key resistance for the bearish signal. '
                                f'A close above ON POC confirms the setup has failed. '
                                f'No new shorts here; manage existing position size.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close above → invalidated'}
                else:
                    read = (f'{period_label} closed at {last_close:.2f}, {pts_gap} pts above ONL ({on_low_s}). '
                            f'Two consecutive closes above ONL confirmed — ONL failed as resistance. '
                            f'Market returned inside overnight range; OA two-sided behaviour active.')
                    guidance = (f'ONL ({on_low_s}) has failed as resistance — two consecutive closes above. '
                                f'Market is back inside the overnight range. '
                                f'Neither side has directional control until ONL is reclaimed or ON POC breaks.')
                    watch = {'price': on_poc, 'label': 'ON POC', 'significance': 'close above → invalidated'}

        elif effective_zone == 'CRITICAL':
            if first_warning and prev_zone is not None:
                read = (f'⚠ {period_label} closed at {last_close:.2f} — first touch of ON POC ({on_poc_s}). '
                        f'Sellers must hold this level as resistance. '
                        f'A second close at or above ON POC confirms breakdown.')
                guidance = (f'First touch of ON POC ({on_poc_s}) — last line of defence for the bearish signal. '
                            f'Sellers must hold here. A second close above confirms full signal failure.')
                watch = {'price': on_poc, 'label': 'ON POC — first touch', 'significance': 'second close above → invalidated'}
            else:
                read = (f'{period_label} closed at {last_close:.2f} — at ON POC ({on_poc_s}). '
                        f'Two consecutive closes at last resistance for the bearish IB signal. '
                        f'A period close above ON POC invalidates the setup completely.')
                guidance = (f'Two consecutive closes at ON POC ({on_poc_s}) — signal at last defence. '
                            f'Bearish thesis requires an immediate close below to survive.')
                watch = {'price': on_poc, 'label': 'ON POC — critical', 'significance': 'close above → invalidated'}

        else:  # INVALIDATED
            pts_above = round(last_close - on_poc, 2)
            if first_warning:
                read = (f'⚠ {period_label} closed at {last_close:.2f}, {pts_above} pts above ON POC ({on_poc_s}) — '
                        f'first close above the last line of defence. '
                        f'One more period here confirms full invalidation.')
                guidance = (f'First close above ON POC ({on_poc_s}) — approaching full invalidation. '
                            f'One more period above confirms the bearish thesis is broken.')
                watch = {'price': on_poc, 'label': 'ON POC — first breach', 'significance': 'second close above → fully invalidated'}
            else:
                read = (f'{period_label} closed at {last_close:.2f}, {pts_above} pts above ON POC ({on_poc_s}). '
                        f'Bearish IB signal invalidated — two consecutive closes above ON POC. '
                        f'ON POC is now support; ONH ({on_high_s}) is the next upside target.')
                guidance = (f'Bearish signal invalidated — two closes above ON POC. '
                            f'ON POC ({on_poc_s}) is now support; overnight buyers in control. '
                            f'Next resistance: ONH ({on_high_s}).')
                watch = {'price': on_high, 'label': 'ONH', 'significance': 'close above → breakout'}

    else:
        # NEUTRAL / OA day
        if on_high and on_low and on_poc:
            if curr_zone == 'CONFIRMED' and last_close > on_high:
                read    = (f'{period_label} closed at {last_close:.2f}, above ONH ({on_high_s}). '
                           f'OA resolved bullish — buyers broke above the overnight range.')
                guidance = f'OA resolved bullish — buyers accepted above ONH ({on_high_s}). Overnight range is now below current price.'
                watch   = {'price': on_high, 'label': 'ONH — now support', 'significance': 'close below → OA resumes'}
            elif curr_zone == 'CONFIRMED' and last_close < on_low:
                read    = (f'{period_label} closed at {last_close:.2f}, below ONL ({on_low_s}). '
                           f'OA resolved bearish — sellers broke below the overnight range.')
                guidance = f'OA resolved bearish — sellers accepted below ONL ({on_low_s}). Overnight range is now above current price.'
                watch   = {'price': on_low, 'label': 'ONL — now resistance', 'significance': 'close above → OA resumes'}
            else:
                status  = 'INTACT'
                read    = (f'{period_label} closed at {last_close:.2f}, within overnight range '
                           f'({on_low_s}–{on_high_s}). OA day continuing — neither side committed.')
                guidance = f'Sell near ONH ({on_high_s}), buy near ONL ({on_low_s}). Wait for close outside range.'
                watch   = (
                    {'price': on_high, 'label': 'ONH', 'significance': 'close above → OA resolved bullish'}
                    if last_close > on_poc else
                    {'price': on_low,  'label': 'ONL', 'significance': 'close below → OA resolved bearish'}
                )
        else:
            return _building

    # ── Developing period — bridge the gap between closes ────────────────────
    # The live read is designed to narrate what's happening RIGHT NOW inside
    # the current in-progress period, not just at the close of each letter.
    next_idx = last_complete_idx + 1
    dev_read = ''
    dev_letter = ''
    dev_price  = None
    if next_idx <= 12:
        dev_letter = chr(ord('A') + next_idx)
        dev_data   = pr.get(dev_letter, {})
        dev_price  = dev_data.get('close')   # current live price of the developing bar

        if dev_price is not None and on_high is not None and on_low is not None and on_poc is not None:
            above_onh = dev_price > on_high + tick
            below_onl = dev_price < on_low  - tick
            below_poc = dev_price < on_poc  - tick
            above_poc = dev_price > on_poc  + tick
            ib_h      = ib_high or on_high
            above_ibh = dev_price > ib_h + tick

            if bias in ('BULLISH', 'BULLISH_LEAN'):
                if above_ibh and status == 'CONFIRMED':
                    pts = round(dev_price - ib_h, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} extending {pts} pts above IB High ({ib_h:.2f}) '
                                f'at {dev_price:.2f}. Trend day in progress — ride longs, '
                                f'trail stop below ONH ({on_high_s}).')
                elif above_ibh and status == 'INTACT':
                    pts = round(dev_price - ib_h, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} re-testing IB High ({ib_h:.2f}), {pts} pts above at {dev_price:.2f}. '
                                f'If {dev_letter} closes here → status returns to CONFIRMED. '
                                f'Trail stop below ONH ({on_high_s}) on open longs.')
                elif above_onh and status in ('WEAKENING', 'INTACT'):
                    pts = round(dev_price - on_high, 2)
                    ib_ref = f'{ib_h:.2f}'
                    if status == 'WEAKENING':
                        dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts above ONH ({on_high_s}) '
                                    f'at {dev_price:.2f}. Close here upgrades from WEAKENING to INTACT — '
                                    f'buyers reclaiming ONH. Close above IB High ({ib_ref}) = CONFIRMED. '
                                    f'Trail stop below ONH on open longs.')
                    else:  # INTACT
                        dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts above ONH ({on_high_s}) '
                                    f'at {dev_price:.2f}. Signal INTACT — buyers holding above ONH. '
                                    f'Close above IB High ({ib_ref}) upgrades to CONFIRMED. '
                                    f'Trail stop below ONH on open longs.')
                elif above_onh and status == 'CONFIRMED':
                    dev_read = (f'\n\n{dev_letter} holding above ONH ({on_high_s}) at {dev_price:.2f}. '
                                f'Trend day continuing — maintain longs.')
                elif below_poc:
                    if status == 'INVALIDATED':
                        dev_read = (f'\n\n⚡ {dev_letter} confirming INVALIDATED at {dev_price:.2f} — '
                                    f'holding below ON POC ({on_poc_s}, now resistance). '
                                    f'Sell rallies to ON POC. Maintain short bias.')
                    else:
                        dev_read = (f'\n\n⚠ {dev_letter} developing below ON POC ({on_poc_s}) '
                                    f'at {dev_price:.2f}. If {dev_letter} closes here → signal INVALIDATED. '
                                    f'Reduce longs immediately.')
                elif dev_price < on_high - tick and status == 'CONFIRMED':
                    pts = round(on_high - dev_price, 2)
                    dev_read = (f'\n\n{dev_letter} pulling back {pts} pts below ONH ({on_high_s}) '
                                f'at {dev_price:.2f}. Watch for close: above ONH = trend intact, '
                                f'below = downgrade.')

            elif bias in ('BEARISH', 'BEARISH_LEAN'):
                ib_l = ib_low or on_low
                below_ibl = dev_price < ib_l - tick
                if below_ibl and status == 'CONFIRMED':
                    pts = round(ib_l - dev_price, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} extending {pts} pts below IB Low ({ib_l:.2f}) '
                                f'at {dev_price:.2f}. Trend day in progress — ride shorts, '
                                f'trail stop above ONL ({on_low_s}).')
                elif below_ibl and status == 'INTACT':
                    pts = round(ib_l - dev_price, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} re-testing IB Low ({ib_l:.2f}), {pts} pts below at {dev_price:.2f}. '
                                f'If {dev_letter} closes here → status returns to CONFIRMED. '
                                f'Trail stop above ONL ({on_low_s}) on open shorts.')
                elif below_onl and status in ('WEAKENING', 'INTACT'):
                    pts = round(on_low - dev_price, 2)
                    ib_ref = f'{ib_l:.2f}'
                    if status == 'WEAKENING':
                        dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts below ONL ({on_low_s}) '
                                    f'at {dev_price:.2f}. Close here upgrades from WEAKENING to INTACT — '
                                    f'sellers reclaiming ONL. Close below IB Low ({ib_ref}) = CONFIRMED. '
                                    f'Trail stop above ONL on open shorts.')
                    else:  # INTACT
                        dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts below ONL ({on_low_s}) '
                                    f'at {dev_price:.2f}. Signal INTACT — sellers holding below ONL. '
                                    f'Close below IB Low ({ib_ref}) upgrades to CONFIRMED. '
                                    f'Trail stop above ONL on open shorts.')
                elif above_poc:
                    if status == 'INVALIDATED':
                        dev_read = (f'\n\n⚡ {dev_letter} confirming INVALIDATED at {dev_price:.2f} — '
                                    f'holding above ON POC ({on_poc_s}, now support). '
                                    f'Buy dips to ON POC. Maintain long bias.')
                    else:
                        dev_read = (f'\n\n⚠ {dev_letter} developing above ON POC ({on_poc_s}) '
                                    f'at {dev_price:.2f}. If {dev_letter} closes here → signal INVALIDATED. '
                                    f'Reduce shorts immediately.')

            else:
                # NEUTRAL (OA day) — narrate live vs overnight range boundaries
                if status == 'CONFIRMED' and dev_price > on_high + tick:
                    pts = round(dev_price - on_high, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts above ONH ({on_high_s}) '
                                f'at {dev_price:.2f}. OA resolving bullish — '
                                f'if {dev_letter} closes above ONH, buy pullbacks to ONH.')
                elif status == 'CONFIRMED' and dev_price < on_low - tick:
                    pts = round(on_low - dev_price, 2)
                    dev_read = (f'\n\n⚡ {dev_letter} developing {pts} pts below ONL ({on_low_s}) '
                                f'at {dev_price:.2f}. OA resolving bearish — '
                                f'if {dev_letter} closes below ONL, sell rallies to ONL.')
                elif dev_price > on_high - tick:
                    dev_read = (f'\n\n{dev_letter} testing ONH ({on_high_s}) at {dev_price:.2f}. '
                                f'Close above = OA resolved bullish. Fade if rejected back inside.')
                elif dev_price < on_low + tick:
                    dev_read = (f'\n\n{dev_letter} testing ONL ({on_low_s}) at {dev_price:.2f}. '
                                f'Close below = OA resolved bearish. Fade if rejected back inside.')
                elif on_poc and dev_price > on_poc + tick:
                    dev_read = (f'\n\n{dev_letter} at {dev_price:.2f}, holding above ON POC ({on_poc_s}). '
                                f'Buyers slightly in control — watch for ONH ({on_high_s}) test.')
                elif on_poc and dev_price < on_poc - tick:
                    dev_read = (f'\n\n{dev_letter} at {dev_price:.2f}, below ON POC ({on_poc_s}). '
                                f'Sellers slightly in control — watch for ONL ({on_low_s}) test.')

    # For neutral IB (ib_score == 0) the CONFIRMED zone can be either bullish
    # (price broke above ONH) or bearish (price broke below ONL).  Resolve the
    # direction here where we have price and overnight range in scope, and pass
    # it to _zone_to_live_adj so it picks the correct sign.
    confirmed_dir = 0
    if ib_score_raw == 0 and effective_zone == 'CONFIRMED':
        if on_high is not None and last_close > on_high:
            confirmed_dir = 1   # broke above overnight range → bullish
        elif on_low is not None and last_close < on_low:
            confirmed_dir = -1  # broke below overnight range → bearish

    live_adj      = _zone_to_live_adj(effective_zone, ib_score_raw, confirmed_dir)
    current_score = ib_score_raw + live_adj
    current_bias, current_label = _score_to_bias(current_score)

    # ── Live trade plan — updates with current_bias + zone ────────────────────
    live_trade_plan = _build_live_trade_plan(
        effective_zone, current_bias,
        on_high, on_low, on_poc, ib_high, ib_low,
    )

    return {
        'active':            True,
        'status':            status,
        'last_period':       last_letter,
        'last_close':        last_close,
        'current_read':      read + dev_read,   # closed period + live developing context
        'live_guidance':     guidance,
        'watch_level':       watch,
        'first_warning':     first_warning,
        'developing_period': dev_letter or None,
        'developing_price':  dev_price,
        'ib_score':          ib_score_raw,
        'live_adjustment':   live_adj,
        'current_score':     current_score,
        'current_bias':      current_bias,
        'current_label':     current_label,
        'live_trade_plan':   live_trade_plan,
    }


@app.get('/api/market-profile/{symbol:path}')
async def api_market_profile(symbol: str):
    """Full Dalton Market Profile for a futures symbol.

    Returns today's developing TPO profile (letters A–M), prior RTH profile,
    overnight context, opening type, day type classification, and the 80% rule.
    """
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    symbol = symbol.upper()
    tick   = _tick_for(symbol)
    now_et = datetime.now(ET)
    today  = now_et.date()

    try:
        raw_1min = await asyncio.to_thread(get_candles, symbol, 5, 1)
    except Exception as exc:
        err_str = str(exc)
        if ('invalid_grant' in err_str or 'refresh_token' in err_str.lower()
                or '401' in err_str
                or 'oauth/token' in err_str          # 400 from failed token refresh
                or 'unsupported_token_type' in err_str
                or 'token is invalid' in err_str.lower()):
            return JSONResponse(status_code=503, content={
                'error': 'token_expired',
                'message': 'Schwab token expired — run renew_schwab_token.py to restore live data.',
            })
        return JSONResponse(status_code=503, content={
            'error': 'schwab_unavailable',
            'message': f'Could not reach Schwab API: {err_str[:120]}',
        })

    # Classify bars into RTH and overnight sessions.
    # All bars belonging to the same overnight session are keyed to the date
    # of the RTH session they LEAD INTO (i.e. the next trading day).
    # Mon–Thu evenings  → next calendar day (Tue–Fri)
    # Fri evening       → Monday  (+3, skipping Sat+Sun)
    # Saturday          → Monday  (+2)
    # Sunday            → Monday  (+1)
    # Weekday pre-market → same calendar day
    rth_by_date: dict = {}
    on_by_date:  dict = {}
    for c in raw_1min:
        dt    = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        d     = dt.date()
        t_min = dt.hour * 60 + dt.minute
        wday  = dt.weekday()          # Mon=0 … Fri=4 … Sat=5 … Sun=6
        is_rth      = wday < 5 and (9 * 60 + 30) <= t_min < 16 * 60
        is_on_wkday = wday < 5 and t_min < (9 * 60 + 30)
        is_evening  = wday < 5 and t_min >= 18 * 60
        if is_rth:
            rth_by_date.setdefault(d, []).append(c)
        elif is_on_wkday:
            on_by_date.setdefault(d, []).append(c)
        elif is_evening:
            days_fwd = 3 if wday == 4 else 1  # Fri→Mon, Mon-Thu→next day
            on_by_date.setdefault(d + timedelta(days=days_fwd), []).append(c)
        elif wday == 5:  # Saturday → Monday
            on_by_date.setdefault(d + timedelta(days=2), []).append(c)
        elif wday == 6:  # Sunday → Monday
            on_by_date.setdefault(d + timedelta(days=1), []).append(c)

    sorted_rth = sorted(rth_by_date.keys(), reverse=True)
    prior_dates = [d for d in sorted_rth if d < today]

    # ── Today's developing RTH profile ────────────────────────────────────────
    today_bars = rth_by_date.get(today, [])
    today_prof = _build_rth_tpo_profile(today_bars, tick)

    # ── Prior RTH profile (yesterday) ─────────────────────────────────────────
    prior_prof: dict = {'profile': [], 'poc': None, 'vah': None, 'val': None,
                        'single_prints': [], 'ib_high': None, 'ib_low': None,
                        'ib_range': None, 'session_high': None, 'session_low': None,
                        'date': None, 'close': None}
    if prior_dates:
        prior_bars = rth_by_date.get(prior_dates[0], [])
        prior_prof = {**_build_rth_tpo_profile(prior_bars, tick),
                      'date': str(prior_dates[0])}
        if prior_bars:
            srt = sorted(prior_bars, key=lambda b: b['datetime'])
            prior_prof['high']  = max(b['high'] for b in prior_bars)
            prior_prof['low']   = min(b['low']  for b in prior_bars)
            prior_prof['close'] = srt[-1]['close']

    # Prior RTH range for day-type normalization
    prior_rth_range = None
    if prior_prof.get('session_high') and prior_prof.get('session_low'):
        prior_rth_range = round(prior_prof['session_high'] - prior_prof['session_low'], 2)

    # ── Prior Overnight (overnight that led INTO the prior RTH session) ───────
    _empty_on: dict = {
        'high': None, 'low': None, 'poc': None, 'vah': None, 'val': None,
        'profile': [], 'single_prints': [], 'periods': 0, 'period_ranges': {},
        'session_high': None, 'session_low': None,
    }
    prior_overnight: dict = dict(_empty_on)
    if prior_dates:
        _p_on_bars = on_by_date.get(prior_dates[0], [])
        if _p_on_bars:
            prior_overnight['high'] = round(max(b['high'] for b in _p_on_bars), 2)
            prior_overnight['low']  = round(min(b['low']  for b in _p_on_bars), 2)
            _p_on_tpo = _compute_tpo_value_area(_p_on_bars, tick)
            prior_overnight['poc'] = _p_on_tpo['poc']
            prior_overnight['vah'] = _p_on_tpo['vah']
            prior_overnight['val'] = _p_on_tpo['val']
            _p_on_prof = _build_overnight_tpo_profile(_p_on_bars, tick)
            prior_overnight.update({k: _p_on_prof[k] for k in
                ('profile', 'single_prints', 'periods', 'period_ranges',
                 'session_high', 'session_low')})

    # ── Overnight context ─────────────────────────────────────────────────────
    # The overnight to display is the session bridging the prior RTH to the
    # next/developing RTH.  During an active RTH session that is today; on
    # weekends or pre-market it is keyed to the *next trading day* after the
    # most recent complete RTH session.
    if today_bars:
        on_date = today
    elif prior_dates:
        # Walk forward from the prior RTH date to find the next trading day
        nxt = prior_dates[0] + timedelta(days=1)
        while nxt.weekday() >= 5:        # skip Sat / Sun
            nxt += timedelta(days=1)
        on_date = nxt
    else:
        on_date = today
    on_bars = on_by_date.get(on_date, [])
    overnight: dict = {
        'high': None, 'low': None, 'poc': None, 'vah': None, 'val': None,
        'profile': [], 'single_prints': [], 'periods': 0, 'period_ranges': {},
        'session_high': None, 'session_low': None,
    }
    if on_bars:
        overnight['high'] = round(max(b['high'] for b in on_bars), 2)
        overnight['low']  = round(min(b['low']  for b in on_bars), 2)
        _on_tpo = _compute_tpo_value_area(on_bars, tick)
        overnight['poc']  = _on_tpo['poc']
        overnight['vah']  = _on_tpo['vah']
        overnight['val']  = _on_tpo['val']
        _on_prof = _build_overnight_tpo_profile(on_bars, tick)
        overnight['profile']       = _on_prof['profile']
        overnight['single_prints'] = _on_prof['single_prints']
        overnight['periods']       = _on_prof['periods']
        overnight['period_ranges'] = _on_prof['period_ranges']
        overnight['session_high']  = _on_prof['session_high']
        overnight['session_low']   = _on_prof['session_low']

    # ── Opening type ──────────────────────────────────────────────────────────
    open_price = None
    if today_bars:
        srt_today = sorted(today_bars, key=lambda b: b['datetime'])
        open_price = srt_today[0]['open']

    a_period = b_period = None
    pr = today_prof.get('period_ranges', {})
    if 'A' in pr:
        a_period = pr['A']
    if 'B' in pr:
        b_period = pr['B']

    # Use IB (A+B combined) for opening type classification; fall back to A-only
    # when B hasn't closed yet so we always have a developing classification.
    ib_high_cl = today_prof.get('ib_high') or (a_period['high'] if a_period else None)
    ib_low_cl  = today_prof.get('ib_low')  or (a_period['low']  if a_period else None)

    opening = _classify_opening(
        open_price or 0.0, ib_high_cl, ib_low_cl,
        prior_prof.get('vah'), prior_prof.get('val'), prior_prof.get('poc'), tick,
    ) if open_price else {'type': 'PREMARKET', 'label': 'Pre-Market',
                          'description': 'RTH has not started yet.', 'inside_prior_va': None}

    # ── Day type ──────────────────────────────────────────────────────────────
    day_type = _classify_day_type(today_prof, prior_rth_range)

    # ── 80 % Rule ─────────────────────────────────────────────────────────────
    current_price = None
    if today_bars:
        srt_today = sorted(today_bars, key=lambda b: b['datetime'])
        current_price = srt_today[-1]['close']
    elif on_bars:
        srt_on = sorted(on_bars, key=lambda b: b['datetime'])
        current_price = srt_on[-1]['close']
    # Fallback: use the most recent bar across all fetched bars (e.g. Sunday with no overnight yet)
    if current_price is None and raw_1min:
        current_price = sorted(raw_1min, key=lambda b: b['datetime'])[-1]['close']

    rule_80 = _check_80pct_rule(
        open_price or 0.0, a_period, b_period,
        prior_prof.get('vah'), prior_prof.get('val'), current_price,
    ) if open_price else {'triggered': False, 'description': 'RTH has not started.'}

    computed_at = now_et.strftime('%-I:%M %p ET')

    # ── IB Signals ────────────────────────────────────────────────────────────
    # Today's signals — gated on 10:30 AM ET so partial B-period data does not
    # trigger a premature IB analysis (period_ranges gains 'B' at 10:05 but B
    # only closes at 10:30).
    ib_signals = _generate_ib_signals(today_prof, overnight, prior_prof, tick, now_et)

    # Prior session signals (Thu overnight → Fri RTH) — always available if data exists
    # The "prior prior RTH" (Wed) isn't tracked, so we pass an empty dict for extension targets
    prior_ib_signals = _generate_ib_signals(prior_prof, prior_overnight, {}, tick)

    # ── Live Read ─────────────────────────────────────────────────────────────
    # Dynamic per-period read that updates with each new letter printed
    live_read = _evaluate_live_read(today_prof, ib_signals, overnight, now_et, tick)

    # ── Dalton Rule Engine ────────────────────────────────────────────────────
    rule_engine = _mp_rules.run_all(today_prof, overnight, prior_prof, tick, now_et)

    result = {
        'symbol':           symbol,
        'tick':             tick,
        'computed_at':      computed_at,
        'current_price':    current_price,
        'today':            today_prof,
        'prior_rth':        prior_prof,
        'prior_overnight':  prior_overnight,
        'overnight':        overnight,
        'opening':          opening,
        'day_type':         day_type,
        'rule_80':          rule_80,
        'ib_signals':       ib_signals,
        'prior_ib_signals': prior_ib_signals,
        'live_read':        live_read,
        'rule_engine':      rule_engine,
    }

    # Pre-market read — replaces the BUILDING dead-state with useful context
    t_min_now = now_et.hour * 60 + now_et.minute
    premarket_read = (
        _build_premarket_read(current_price, overnight, prior_prof, now_et, tick)
        if t_min_now < 9 * 60 + 30
        else {'active': False}
    )
    result['premarket_read'] = premarket_read

    # Cache for Ask AI context — keyed by symbol, always fresh from the frontend 60s poll
    state['market_profile'][symbol] = result
    return result


def _build_premarket_read(
    current_price: float | None,
    overnight: dict,
    prior_rth: dict,
    now_et,
    tick: float,
) -> dict:
    """Pre-market Dalton context: gap analysis, overnight inventory, opening scenario preview.

    Replaces the BUILDING dead-state before RTH with actionable information:
      - Gap classification vs prior RTH value area
      - Overnight inventory position (which third of overnight range)
      - Most probable opening type (OD/OTD/OA/ORR) and guidance
      - Key levels to watch at the open
      - Minutes to open countdown
    """
    if current_price is None:
        return {'active': False}

    on_high   = overnight.get('high')
    on_low    = overnight.get('low')
    on_poc    = overnight.get('poc')
    on_vah    = overnight.get('vah')
    on_val    = overnight.get('val')
    prior_vah = prior_rth.get('vah')
    prior_val = prior_rth.get('val')
    prior_poc = prior_rth.get('poc')

    if not all([on_high, on_low, prior_vah, prior_val]):
        return {'active': False}

    on_range = on_high - on_low

    # ── 1. Gap analysis vs prior RTH value area ──────────────────────────────
    if current_price >= prior_vah - tick:
        gap_type  = 'ABOVE_VALUE'
        gap_pts   = round(current_price - prior_vah, 2)
        gap_label = f'Gap Up {gap_pts} pts — Above Prior VAH ({prior_vah:.2f})'
        gap_bias  = 'BULLISH'
    elif current_price <= prior_val + tick:
        gap_type  = 'BELOW_VALUE'
        gap_pts   = round(prior_val - current_price, 2)
        gap_label = f'Gap Down {gap_pts} pts — Below Prior VAL ({prior_val:.2f})'
        gap_bias  = 'BEARISH'
    else:
        gap_type  = 'INSIDE_VALUE'
        gap_pts   = 0.0
        gap_label = f'Inside Prior Value Area ({prior_val:.2f}–{prior_vah:.2f})'
        gap_bias  = 'NEUTRAL'

    # ── 2. Overnight inventory position ──────────────────────────────────────
    position_pct = round(((current_price - on_low) / on_range) * 100, 1) if on_range > 0 else 50.0
    position_pct = max(0.0, min(100.0, position_pct))

    if position_pct >= 67:
        inv_pos   = 'UPPER_THIRD'
        inv_label = 'Upper third of ON range — overnight longs right'
        inv_bias  = 'BULLISH'
    elif position_pct <= 33:
        inv_pos   = 'LOWER_THIRD'
        inv_label = 'Lower third of ON range — overnight shorts right'
        inv_bias  = 'BEARISH'
    else:
        inv_pos   = 'MIDDLE'
        inv_label = 'Middle of ON range — balanced overnight'
        inv_bias  = 'NEUTRAL'

    # ── 3. Opening scenario preview ──────────────────────────────────────────
    pval_s = f'{prior_val:.2f}'
    pvah_s = f'{prior_vah:.2f}'
    ppoc_s = f'{prior_poc:.2f}' if prior_poc else 'n/a'

    if gap_type == 'ABOVE_VALUE':
        if inv_bias == 'BULLISH':
            expected_open = 'OD ↑ or OTD ↑'
            open_guidance = (f'Buyers in full control overnight. '
                             f'Watch A period vs Prior VAH ({pvah_s}): acceptance above = trend day up, do not fade. '
                             f'A period reversal back below Prior VAH = ORR short setup.')
        else:
            expected_open = 'ORR ↓ likely'
            open_guidance = (f'Gapped above value but overnight longs fading (price in lower ON range). '
                             f'Watch A period: reversal back below Prior VAH ({pvah_s}) = ORR short. '
                             f'Target Prior POC ({ppoc_s}) then Prior VAL ({pval_s}).')
    elif gap_type == 'BELOW_VALUE':
        if inv_bias == 'BEARISH':
            expected_open = 'OD ↓ or OTD ↓'
            open_guidance = (f'Sellers in full control overnight. '
                             f'Watch A period vs Prior VAL ({pval_s}): acceptance below = trend day down, do not fade. '
                             f'A period reversal back above Prior VAL = ORR long setup.')
        else:
            expected_open = 'ORR ↑ likely'
            open_guidance = (f'Gapped below value but overnight shorts fading (price in upper ON range). '
                             f'Watch A period: reversal back above Prior VAL ({pval_s}) = ORR long. '
                             f'Target Prior POC ({ppoc_s}) then Prior VAH ({pvah_s}).')
    else:
        expected_open = 'OA — Open Auction'
        open_guidance = (f'Price inside prior value area ({pval_s}–{pvah_s}) — overnight buyers and sellers balanced. '
                         f'Two-sided auction expected. '
                         f'Buy Prior VAL ({pval_s}), sell Prior VAH ({pvah_s}) until a decisive break outside value.')

    # ── 4. Minutes to open ───────────────────────────────────────────────────
    RTH_START    = 9 * 60 + 30
    t_min        = now_et.hour * 60 + now_et.minute
    mins_to_open = max(0, RTH_START - t_min)

    # ── 5. Key levels (sorted high → low) ────────────────────────────────────
    _kl: list[dict] = []
    if prior_vah: _kl.append({'level': prior_vah, 'label': 'Prior VAH',     'role': 'prior_value'})
    if prior_poc: _kl.append({'level': prior_poc, 'label': 'Prior RTH POC', 'role': 'pivot'})
    if prior_val: _kl.append({'level': prior_val, 'label': 'Prior VAL',     'role': 'prior_value'})
    if on_high:   _kl.append({'level': on_high,   'label': 'ONH',           'role': 'overnight'})
    if on_vah:    _kl.append({'level': on_vah,    'label': 'ON VAH',        'role': 'overnight'})
    if on_poc:    _kl.append({'level': on_poc,    'label': 'ON POC',        'role': 'overnight'})
    if on_val:    _kl.append({'level': on_val,    'label': 'ON VAL',        'role': 'overnight'})
    if on_low:    _kl.append({'level': on_low,    'label': 'ONL',           'role': 'overnight'})
    _kl.sort(key=lambda x: x['level'], reverse=True)

    return {
        'active':        True,
        'gap_type':      gap_type,
        'gap_bias':      gap_bias,
        'gap_label':     gap_label,
        'gap_pts':       gap_pts,
        'inv_pos':       inv_pos,
        'inv_label':     inv_label,
        'inv_bias':      inv_bias,
        'position_pct':  position_pct,
        'on_range':      round(on_range, 2),
        'expected_open': expected_open,
        'open_guidance': open_guidance,
        'mins_to_open':  mins_to_open,
        'key_levels':    _kl,
        'prior_vah':     prior_vah,
        'prior_val':     prior_val,
        'prior_poc':     prior_poc,
        'on_high':       on_high,
        'on_low':        on_low,
        'on_poc':        on_poc,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# User Management
# ═══════════════════════════════════════════════════════════════════════════════

ADMIN_EMAIL         = 'wassim.hamwi@outlook.com'
RESEND_API_KEY      = os.environ.get('RESEND_API_KEY', '')
ADMIN_APPROVAL_SECRET = os.environ.get('ADMIN_APPROVAL_SECRET', 'change-me-in-railway')
SITE_URL            = os.environ.get('SITE_URL', 'https://domytrade.app')

def _send_email(to: str, subject: str, html: str) -> bool:
    """Send email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        logging.warning('RESEND_API_KEY not set — skipping email to %s', to)
        return False
    try:
        import httpx
        r = httpx.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json={'from': 'DoMyTrade <noreply@domytrade.app>', 'to': [to], 'subject': subject, 'html': html},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        logging.error('Email send failed: %s', exc)
        return False


def _get_supabase_admin():
    """Return a Supabase client with service role key for admin operations."""
    from supabase import create_client
    return create_client(
        os.environ['SUPABASE_URL'],
        os.environ['SUPABASE_SERVICE_ROLE_KEY'],
    )


@app.post('/api/auth/notify-admin')
async def notify_admin(request: _Request):
    """Called from frontend after email verification.
    Updates profile status to pending_approval and emails the admin.
    Idempotent — safe to call multiple times."""
    from fastapi import HTTPException

    # Extract user from JWT
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing token')
    token = auth_header.split(' ', 1)[1]

    try:
        sb = _get_supabase_admin()
        user_resp = sb.auth.get_user(token)
        user = user_resp.user
        if not user:
            raise HTTPException(status_code=401, detail='Invalid token')
    except Exception:
        raise HTTPException(status_code=401, detail='Invalid token')

    # Get profile
    profile_resp = sb.table('user_profiles').select('*').eq('id', str(user.id)).single().execute()
    profile = profile_resp.data if profile_resp.data else None
    if not profile:
        raise HTTPException(status_code=404, detail='Profile not found')

    # Already approved — nothing to do
    if profile['status'] == 'approved':
        return {'status': 'already_approved'}

    # Update to pending_approval
    sb.table('user_profiles').update({'status': 'pending_approval'}).eq('id', str(user.id)).execute()

    # Send admin notification (only if transitioning from pending_verification)
    if profile['status'] == 'pending_verification':
        name  = profile.get('full_name', 'Unknown')
        email = profile.get('email', user.email or '')
        phone = profile.get('phone') or 'Not provided'
        approve_url = f"{SITE_URL}/api/admin/approve/{user.id}?secret={ADMIN_APPROVAL_SECRET}"
        reject_url  = f"{SITE_URL}/api/admin/reject/{user.id}?secret={ADMIN_APPROVAL_SECRET}"

        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;">
          <h2 style="color:#3b82f6;margin-bottom:4px;">◈ DoMyTrade</h2>
          <h3 style="color:#111;margin-top:0;">New Access Request</h3>
          <table style="width:100%;border-collapse:collapse;margin:16px 0;">
            <tr><td style="padding:6px 0;color:#666;width:100px;">Name</td><td style="padding:6px 0;font-weight:600;">{name}</td></tr>
            <tr><td style="padding:6px 0;color:#666;">Email</td><td style="padding:6px 0;">{email}</td></tr>
            <tr><td style="padding:6px 0;color:#666;">Phone</td><td style="padding:6px 0;">{phone}</td></tr>
          </table>
          <div style="display:flex;gap:12px;margin-top:24px;">
            <a href="{approve_url}" style="background:#22c55e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">✓ Approve</a>
            <a href="{reject_url}" style="background:#ef4444;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">✕ Reject</a>
          </div>
        </div>
        """
        _send_email(ADMIN_EMAIL, f'[DoMyTrade] Access request from {name}', html)

    return {'status': 'pending_approval'}


@app.get('/api/admin/approve/{user_id}')
async def approve_user(user_id: str, secret: str = ''):
    """Admin clicks this link from email to approve a user."""
    from fastapi.responses import HTMLResponse
    if secret != ADMIN_APPROVAL_SECRET:
        return HTMLResponse('<h2>Invalid or expired link.</h2>', status_code=403)

    try:
        sb = _get_supabase_admin()
        resp = sb.table('user_profiles').update({
            'status': 'approved',
            'approved_at': datetime.now(timezone.utc).isoformat(),
            'approved_by': ADMIN_EMAIL,
        }).eq('id', user_id).execute()

        if not resp.data:
            return HTMLResponse('<h2>User not found.</h2>', status_code=404)

        profile = resp.data[0]
        user_email = profile.get('email', '')
        user_name  = profile.get('full_name', 'Trader')

        # Send welcome email to user
        html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;">
          <h2 style="color:#3b82f6;margin-bottom:4px;">◈ DoMyTrade</h2>
          <h3 style="color:#111;margin-top:0;">Access Approved!</h3>
          <p style="color:#444;">Hi {user_name}, your access to DoMyTrade has been approved.</p>
          <a href="{SITE_URL}/login" style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">
            Sign In Now →
          </a>
          <p style="color:#888;font-size:12px;margin-top:24px;">DoMyTrade — Professional Trading Signals</p>
        </div>
        """
        _send_email(user_email, 'Your DoMyTrade access has been approved', html)

        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
          <h2 style="color:#22c55e;">✓ {user_name} approved</h2>
          <p>A welcome email has been sent to {user_email}.</p>
        </body></html>
        """)
    except Exception as exc:
        logging.error('Approve user error: %s', exc)
        return HTMLResponse('<h2>Error — check logs.</h2>', status_code=500)


@app.get('/api/admin/reject/{user_id}')
async def reject_user(user_id: str, secret: str = ''):
    """Admin clicks this link to reject a user."""
    from fastapi.responses import HTMLResponse
    if secret != ADMIN_APPROVAL_SECRET:
        return HTMLResponse('<h2>Invalid or expired link.</h2>', status_code=403)

    try:
        sb = _get_supabase_admin()
        resp = sb.table('user_profiles').update({'status': 'rejected'}).eq('id', user_id).execute()
        if not resp.data:
            return HTMLResponse('<h2>User not found.</h2>', status_code=404)

        profile  = resp.data[0]
        user_name = profile.get('full_name', 'Applicant')

        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
          <h2 style="color:#ef4444;">✕ {user_name} rejected</h2>
        </body></html>
        """)
    except Exception as exc:
        logging.error('Reject user error: %s', exc)
        return HTMLResponse('<h2>Error — check logs.</h2>', status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# Schwab Account / Positions / Orders  (read-only)
# ─────────────────────────────────────────────────────────────────────────────

@app.get('/api/account/numbers')
async def api_account_numbers():
    try:
        data = await asyncio.to_thread(_trader_get, '/accounts/accountNumbers')
        return {'accounts': data}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


@app.get('/api/account/raw')
async def api_account_raw():
    """Full raw Schwab account response (diagnostics — shows all keys)."""
    try:
        data = await asyncio.to_thread(_trader_get, '/accounts', {'fields': 'positions'})
        return {'raw': data}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


@app.get('/api/account/summary')
async def api_account_summary():
    """All linked Schwab accounts with balances and positions."""
    try:
        accounts = await asyncio.to_thread(get_accounts)
        return {'accounts': accounts}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


@app.get('/api/account/{account_number}/positions')
async def api_positions(account_number: str):
    """Open positions for a specific account."""
    try:
        positions = await asyncio.to_thread(get_positions, account_number)
        return {'positions': positions}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


@app.get('/api/account/{account_number}/orders')
async def api_orders(account_number: str, status: str | None = None):
    """Recent orders for a specific account. Optional ?status=WORKING|FILLED|CANCELED etc."""
    try:
        orders = await asyncio.to_thread(get_orders, account_number, 50, status)
        return {'orders': orders}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


@app.get('/api/account/{account_number}/transactions')
async def api_transactions(account_number: str, days: int = 30):
    """Trade transaction history for a specific account."""
    try:
        txns = await asyncio.to_thread(get_transactions, account_number, days)
        return {'transactions': txns}
    except Exception as exc:
        return JSONResponse(status_code=503, content={'error': str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Trading Bot — price-triggered VBH futures auto-execution
# ─────────────────────────────────────────────────────────────────────────────

import datetime as _dt

_BOT_ALLOWED_ASSETS = {'/MES', '/MNQ', '/M2K', '/MYM', '/MGC'}
_BOT_ALLOWED_MODELS = {'CON', 'AGG', 'WIDE'}


class TradingBot:
    """
    Price-triggered VBH futures bot.

    Flow:
      1. Every 30 s — VBH engine recomputes signals; bot picks up the entry
         level (cyan line) and side from the signal's 'entry' field.
      2. When signal is NEAR or ENTRY → bot ARMS: stores the entry price level.
      3. Every 5 s — bot reads state['last_price'] (live feed) and compares
         against the armed entry level.
      4. Price crosses the level → MARKET order fired immediately, no waiting
         for VBH to re-confirm.
      5. Signal drops to NEUTRAL → disarm (cancel if no position).
      6. Stop is a Schwab TRIGGER child order at entry ± stop_pts.
    """

    MAX_LOG = 50
    DEFAULT_ASSET    = '/MES'
    DEFAULT_MODEL    = 'CON'
    DEFAULT_STOP_PTS = 10.0
    DEFAULT_QTY      = 1

    def __init__(self):
        self.enabled        = False
        self.account_number = None
        self.position       = None   # dict when in a trade
        self.armed          = None   # dict when watching for price trigger
        self._log: list     = []
        self.cfg = {
            'asset':    self.DEFAULT_ASSET,
            'model':    self.DEFAULT_MODEL,
            'stop_pts': self.DEFAULT_STOP_PTS,
            'quantity': self.DEFAULT_QTY,
        }

    # ── public API ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        sig = self._find_signal()
        sym_id = self._symbol_id()
        live_px = state['last_price'].get(sym_id) if sym_id else None
        return {
            'enabled':        self.enabled,
            'account_number': self.account_number,
            'cfg':            self.cfg,
            'armed':          self.armed,
            'position':       self.position,
            'live_price':     live_px,
            'signal':         sig,
            'log':            self._log[-20:],
        }

    def enable(self, asset: str, model: str, stop_pts: float, quantity: int) -> dict:
        asset = asset.upper().strip()
        model = model.upper().strip()
        if asset not in _BOT_ALLOWED_ASSETS:
            return {'error': f'Asset {asset} not allowed. Choose: {sorted(_BOT_ALLOWED_ASSETS)}'}
        if model not in _BOT_ALLOWED_MODELS:
            return {'error': f'Model {model} not valid. Choose: {sorted(_BOT_ALLOWED_MODELS)}'}
        if not (0 < stop_pts <= 50):
            return {'error': 'stop_pts must be 1–50'}
        if not (1 <= quantity <= 10):
            return {'error': 'quantity must be 1–10'}

        self.cfg = {'asset': asset, 'model': model,
                    'stop_pts': stop_pts, 'quantity': quantity}
        if not self.enabled:
            self._resolve_account()
            self.enabled = True
            self.armed   = None
            self._log_event('INFO',
                f'ENABLED — {asset} {model} | {quantity}ct | {stop_pts}pt stop | watching price')
        else:
            self._log_event('INFO',
                f'Config updated — {asset} {model} | {quantity}ct | {stop_pts}pt stop')
        return self.status()

    def disable(self) -> dict:
        if self.enabled:
            self.enabled = False
            self.armed   = None
            self._log_event('INFO', 'DISABLED — position (if any) left for manual management')
        return self.status()

    # ── 5-second tick ─────────────────────────────────────────────────────────

    async def tick(self):
        if not self.enabled:
            return

        sig      = self._find_signal()
        sig_state = sig.get('signal_state') if sig else None
        sig_side  = sig.get('side')         if sig else None
        sig_entry = sig.get('entry')        if sig else None   # cyan line price
        sig_ref   = sig.get('is_reference', False) if sig else False

        # ── check if Schwab already filled our stop ───────────────────────────
        if self.position:
            await self._check_stop_filled()

        # ── arm / disarm ──────────────────────────────────────────────────────
        _entry_level_reset = False
        if not self.position:
            if sig_state in ('NEAR', 'ENTRY') and sig_entry and sig_side and not sig_ref:
                if not self.armed:
                    self.armed = {
                        'side':        sig_side,
                        'entry_level': sig_entry,
                    }
                    self._log_event('INFO',
                        f'ARMED — {sig_side}  entry level {sig_entry:.2f}  '
                        f'({sig_state})')
                    _entry_level_reset = True
                elif self.armed['side'] != sig_side:
                    # Signal flipped direction while armed — re-arm
                    self.armed = {'side': sig_side, 'entry_level': sig_entry}
                    self._log_event('INFO',
                        f'Re-armed opposite side — {sig_side} @ {sig_entry:.2f}')
                    _entry_level_reset = True
                elif abs(self.armed['entry_level'] - float(sig_entry)) > 0.001:
                    # Entry level shifted (h_high/h_low updated intraday) — hold 1 cycle
                    # before checking trigger so we don't fire when the level moved to us.
                    old = self.armed['entry_level']
                    self.armed['entry_level'] = float(sig_entry)
                    self._log_event('INFO',
                        f'Entry level shifted {old:.2f} → {sig_entry:.2f} — holding 1 cycle')
                    _entry_level_reset = True
            else:
                if self.armed:
                    self._log_event('INFO', 'Signal neutral — disarmed')
                self.armed = None

        # ── price trigger ─────────────────────────────────────────────────────
        if self.armed and not self.position and not _entry_level_reset:
            sym_id   = self._symbol_id()
            live_px  = state['last_price'].get(sym_id) if sym_id else None

            if live_px:
                side        = self.armed['side']
                entry_level = self.armed['entry_level']
                triggered   = (
                    (side == 'LONG'  and live_px <= entry_level) or
                    (side == 'SHORT' and live_px >= entry_level)
                )
                if triggered:
                    self._log_event('TRADE',
                        f'Price trigger — live {live_px:.2f} crossed entry level {entry_level:.2f} ({side})')
                    self.armed = None
                    await self._enter(side, live_px)

        # ── exit management (in position) ─────────────────────────────────────
        if self.position:
            pos_side = self.position['side']
            if sig_state in ('NEAR', 'ENTRY') and sig_side and sig_side != pos_side:
                self._log_event('INFO', f'Signal flipped to {sig_side} — closing {pos_side}')
                await self._exit('signal_flip')
                # Re-arm immediately on the new side
                if sig_entry:
                    self.armed = {'side': sig_side, 'entry_level': sig_entry}
            elif sig_state not in ('NEAR', 'ENTRY'):
                self._log_event('INFO', f'Signal gone — exiting')
                await self._exit('signal_exit')

    # ── execution ─────────────────────────────────────────────────────────────

    async def _enter(self, side: str, price: float):
        stop_pts    = self.cfg['stop_pts']
        qty         = self.cfg['quantity']
        symbol      = front_month_code(self.cfg['asset'])
        instruction = 'BUY' if side == 'LONG' else 'SELL'
        stop_price  = round(price - stop_pts, 2) if side == 'LONG' \
                      else round(price + stop_pts, 2)

        self._log_event('TRADE',
            f'ENTER {side} {symbol} @ ~{price:.2f}  stop={stop_price:.2f}  qty={qty}')
        try:
            result   = await asyncio.to_thread(
                place_futures_order,
                self.account_number, symbol, instruction, qty, stop_price,
            )
            order_id = (result or {}).get('order_id')
            self.position = {
                'symbol':      symbol,
                'side':        side,
                'entry_price': price,
                'stop_price':  stop_price,
                'quantity':    qty,
                'order_id':    order_id,
                'entered_at':  _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            self._log_event('INFO', f'Order submitted — id={order_id}')
        except Exception as exc:
            self._log_event('ERROR', f'Enter failed: {exc}')

    async def _exit(self, reason: str):
        if not self.position:
            return
        pos         = self.position
        close_instr = 'SELL' if pos['side'] == 'LONG' else 'BUY'
        self._log_event('TRADE', f'EXIT {pos["side"]} {pos["symbol"]} — {reason}')
        try:
            if pos.get('order_id'):
                await asyncio.to_thread(cancel_order, self.account_number, pos['order_id'])
            await asyncio.to_thread(
                close_futures_position,
                self.account_number, pos['symbol'], close_instr, pos['quantity'],
            )
            self._log_event('INFO',
                f'Close order submitted — {close_instr} {pos["quantity"]} {pos["symbol"]}')
        except Exception as exc:
            self._log_event('ERROR', f'Exit failed: {exc}')
        finally:
            self.position = None

    async def emergency_close(self) -> dict:
        if self.position:
            await self._exit('emergency_close')
        return self.status()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _find_signal(self) -> dict | None:
        asset = self.cfg['asset']
        model = self.cfg['model']
        for sig in state.get('signals', []):
            ticker = sig.get('symbol', '').split(':')[0]
            if ticker == asset and sig.get('model') == model:
                return sig
        return None

    def _symbol_id(self) -> int | None:
        asset = self.cfg['asset']
        for sym in state.get('symbols', []):
            if sym.get('ticker') == asset:
                return sym.get('id')
        return None

    def _resolve_account(self):
        try:
            acct_list = _trader_get('/accounts/accountNumbers')
            if acct_list:
                self.account_number = acct_list[0].get('hashValue')
        except Exception as exc:
            self._log_event('ERROR', f'Account resolve failed: {exc}')

    async def _check_stop_filled(self):
        if not self.position or not self.account_number:
            return
        try:
            orders = await asyncio.to_thread(get_orders, self.account_number, 20, 'FILLED')
            oid = str(self.position.get('order_id', ''))
            for o in (orders or []):
                if str(o.get('orderId', '')) == oid:
                    for child in o.get('childOrderStrategies', []):
                        if child.get('status') == 'FILLED':
                            self._log_event('INFO', 'Stop FILLED by Schwab — position closed')
                            self.position = None
                            return
        except Exception:
            pass

    def _log_event(self, level: str, msg: str):
        log.info('[BOT] %s', msg)
        self._log.append({
            'ts':    _dt.datetime.now(_dt.timezone.utc).isoformat(),
            'level': level,
            'msg':   msg,
        })
        if len(self._log) > self.MAX_LOG:
            self._log = self._log[-self.MAX_LOG:]


_bot = TradingBot()


async def _bot_loop():
    """Ticks every 5 s — matches the live price feed frequency."""
    while True:
        try:
            await _bot.tick()
        except Exception as exc:
            log.error('bot loop error: %s', exc)
        await asyncio.sleep(5)


# ─── Bot API ──────────────────────────────────────────────────────────────────

@app.get('/api/bot/status')
async def api_bot_status():
    return _bot.status()


class BotEnableRequest(BaseModel):
    asset:    str   = '/MES'
    model:    str   = 'CON'
    stop_pts: float = 10.0
    quantity: int   = 1

@app.post('/api/bot/enable')
async def api_bot_enable(req: BotEnableRequest):
    return _bot.enable(req.asset, req.model, req.stop_pts, req.quantity)


@app.post('/api/bot/disable')
async def api_bot_disable():
    return _bot.disable()


@app.post('/api/bot/close')
async def api_bot_close():
    """Emergency: immediately close the current bot position at market."""
    return await _bot.emergency_close()


# ── equity bot ───────────────────────────────────────────────────────────────

class EquityBot:
    """
    VBH signal-triggered equity bot.

    Arms on NEAR/ENTRY for the configured ticker + model.
    Fires MARKET + STOP bracket when live price crosses the VBH entry level.
    stop_pts is in cents (100 = $1.00 stop).
    """
    MAX_LOG       = 50
    DEFAULT_SYM   = 'BA'
    DEFAULT_MODEL = 'CON'
    DEFAULT_STOP  = 100    # cents → $1.00
    DEFAULT_QTY   = 1

    def __init__(self):
        self.enabled        = False
        self.account_number = None   # Schwab account hash
        self.position       = None
        self.armed          = None
        self._log: list     = []
        self.cfg = {
            'symbol':   self.DEFAULT_SYM,
            'model':    self.DEFAULT_MODEL,
            'stop_pts': self.DEFAULT_STOP,
            'quantity': self.DEFAULT_QTY,
        }

    def _log_event(self, level: str, msg: str):
        self._log.append({
            'ts': _dt.datetime.now(_dt.timezone.utc).isoformat(),
            'level': level, 'msg': msg,
        })
        if len(self._log) > self.MAX_LOG:
            self._log = self._log[-self.MAX_LOG:]

    def status(self) -> dict:
        sig    = self._find_signal()
        sym_id = self._symbol_id()
        live   = state['last_price'].get(sym_id) if sym_id else None
        return {
            'enabled':    self.enabled,
            'cfg':        self.cfg,
            'armed':      self.armed,
            'position':   self.position,
            'live_price': live,
            'signal':     sig,
            'log':        self._log[-20:],
        }

    def _find_signal(self) -> dict | None:
        symbol = self.cfg['symbol'].upper()
        model  = self.cfg['model']
        for sig in state.get('signals', []):
            ticker = sig.get('symbol', '').split(':')[0].upper()
            if ticker == symbol and sig.get('model') == model:
                return sig
        return None

    def _symbol_id(self) -> int | None:
        symbol = self.cfg['symbol'].upper()
        for sym in state.get('symbols', []):
            if sym.get('ticker', '').upper() == symbol:
                return sym.get('id')
        return None

    def _resolve_account(self):
        try:
            acct_list = _trader_get('/accounts/accountNumbers')
            if acct_list:
                self.account_number = acct_list[0].get('hashValue')
        except Exception as exc:
            self._log_event('ERROR', f'Account resolve failed: {exc}')

    def enable(self, symbol: str, model: str, stop_pts: int, quantity: int) -> dict:
        symbol = symbol.upper().strip()
        model  = model.upper().strip()
        if model not in ('CON', 'AGG', 'WIDE'):
            return {'error': 'Model must be CON, AGG, or WIDE'}
        if not (1 <= stop_pts <= 10000):
            return {'error': 'stop_pts must be 1–10000'}
        if not (1 <= quantity <= 500):
            return {'error': 'quantity must be 1–500'}
        self.cfg = {'symbol': symbol, 'model': model,
                    'stop_pts': stop_pts, 'quantity': quantity}
        if not self.enabled:
            self._resolve_account()
            self.enabled = True
            self.armed   = None
            self._log_event('INFO',
                f'ENABLED — {symbol} {model} | {quantity}sh | {stop_pts}¢ stop')
        else:
            self._log_event('INFO',
                f'Config updated — {symbol} {model} | {quantity}sh | {stop_pts}¢ stop')
        return self.status()

    def disable(self) -> dict:
        if self.enabled:
            self.enabled = False
            self.armed   = None
            self._log_event('INFO', 'DISABLED')
        return self.status()

    async def emergency_close(self) -> dict:
        if not self.position or not self.account_number:
            return self.status()
        pos         = self.position
        close_instr = 'SELL' if pos['side'] == 'LONG' else 'BUY'
        self._log_event('TRADE', f'EMERGENCY CLOSE {pos["symbol"]}')
        try:
            if pos.get('order_id'):
                await asyncio.to_thread(cancel_order, self.account_number, str(pos['order_id']))
            await asyncio.to_thread(
                close_equity_position,
                self.account_number, pos['symbol'], close_instr, pos['quantity'],
            )
        except Exception as exc:
            self._log_event('ERROR', f'Emergency close error: {exc}')
        self.position = None
        self.armed    = None
        return self.status()

    async def tick(self):
        if not self.enabled:
            return

        # ── Stale-signal guard ────────────────────────────────────────────────
        # Refuse to arm or stay armed if the signal engine hasn't refreshed
        # recently.  Stale signals can carry wrong levels from a prior session
        # (e.g. after a Schwab auth failure wipes the 1-min bar cache).
        # Normal refresh cycle is ~30 s; allow up to 2 min (4 missed cycles).
        _last_refresh = state.get('last_signal_update')
        if _last_refresh:
            try:
                _age_s = (_dt.datetime.now(_dt.timezone.utc) -
                          _dt.datetime.fromisoformat(_last_refresh).astimezone(_dt.timezone.utc)
                         ).total_seconds()
                if _age_s > 120:
                    if self.armed:
                        self._log_event('WARN',
                            f'Signal data stale ({int(_age_s)}s) — disarmed, will not execute')
                        self.armed = None
                    return   # skip this tick entirely until signals are fresh
            except Exception:
                pass

        sig       = self._find_signal()
        sig_state = sig.get('signal_state') if sig else None
        sig_side  = sig.get('side')         if sig else None
        sig_entry = sig.get('entry')        if sig else None
        sig_ref   = sig.get('is_reference', False) if sig else False

        if self.position:
            await self._check_stop_filled()

        _entry_level_reset = False   # True when armed state is new or entry level shifted
        if not self.position:
            if sig_state in ('NEAR', 'ENTRY') and sig_entry and sig_side and not sig_ref:
                bot_side = 'LONG' if 'LONG' in sig_side else 'SHORT'
                if not self.armed or self.armed.get('side') != bot_side:
                    self.armed = {
                        'side':        bot_side,
                        'entry_level': float(sig_entry),
                        'stop':        sig.get('stop'),
                        't1':          sig.get('t1'),
                    }
                    self._log_event('INFO',
                        f'ARMED {bot_side} entry={sig_entry:.2f} '
                        f'stop={sig.get("stop")} t1={sig.get("t1")} ({sig_state})')
                    _entry_level_reset = True
                elif abs(self.armed['entry_level'] - float(sig_entry)) > 0.001:
                    # Entry level shifted because h_high/h_low updated intraday.
                    # If the new level is already beyond live price (entry moved to us,
                    # not price moved to entry), firing immediately would be wrong.
                    # Log the shift and skip the trigger check this tick.
                    old = self.armed['entry_level']
                    self.armed['entry_level'] = float(sig_entry)
                    self._log_event('INFO',
                        f'Entry level shifted {old:.2f} → {sig_entry:.2f} — holding 1 cycle')
                    _entry_level_reset = True
            else:
                if self.armed:
                    self._log_event('INFO', 'Signal neutral — disarmed')
                self.armed = None

        if self.armed and not self.position and not _entry_level_reset:
            sym_id  = self._symbol_id()
            live_px = state['last_price'].get(sym_id) if sym_id else None
            if live_px:
                side        = self.armed['side']
                entry_level = self.armed['entry_level']
                triggered   = (
                    (side == 'LONG'  and live_px <= entry_level) or
                    (side == 'SHORT' and live_px >= entry_level)
                )
                if triggered:
                    self._log_event('TRADE',
                        f'Price trigger — live {live_px:.2f} crossed {entry_level:.2f}')
                    armed_snap = self.armed   # snapshot before clearing
                    self.armed = None
                    await self._enter(side, live_px, entry_level, armed_snap)

        if self.position:
            pos_side = self.position['side']
            if sig_state in ('NEAR', 'ENTRY') and sig_side:
                bot_side = 'LONG' if 'LONG' in sig_side else 'SHORT'
                if bot_side != pos_side:
                    self._log_event('INFO', f'Signal flipped — exiting')
                    await self._exit('signal_flip')
            elif sig_state not in ('NEAR', 'ENTRY'):
                self._log_event('INFO', 'Signal gone — exiting')
                await self._exit('signal_exit')

    async def _enter(self, side: str, price: float, entry_level: float,
                     armed_snap: dict | None = None):
        symbol      = self.cfg['symbol']
        qty         = self.cfg['quantity']
        instruction = 'BUY' if side == 'LONG' else 'SELL'

        # Prefer signal-derived stop; fall back to config stop_pts dollar amount
        sig_stop = (armed_snap or {}).get('stop')
        sig_t1   = (armed_snap or {}).get('t1')
        if sig_stop is not None:
            stop_price = round(float(sig_stop), 2)
        else:
            stop_dollar = self.cfg['stop_pts'] / 100.0
            stop_price  = round(price - stop_dollar if side == 'LONG'
                                else price + stop_dollar, 2)
        t1_price = round(float(sig_t1), 2) if sig_t1 is not None else None

        self._log_event('TRADE',
            f'ENTER {side} {symbol} qty={qty} @ ~{price:.2f} '
            f' stop={stop_price:.2f}'
            f'{f"  t1={t1_price:.2f}" if t1_price else ""}')
        try:
            result   = await asyncio.to_thread(
                place_equity_order,
                self.account_number, symbol, instruction, qty, stop_price, t1_price,
            )
            if result and '_http_error' in result:
                self._log_event('ERROR',
                    f'Order rejected {result["_http_error"]}: {result.get("_message","")[:120]}')
                return
            order_id = (result or {}).get('order_id')
            self.position = {
                'symbol':      symbol,
                'side':        side,
                'entry_price': price,
                'stop_price':  stop_price,
                'quantity':    qty,
                'order_id':    order_id,
                'entered_at':  _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            self._log_event('INFO', f'Order submitted order_id={order_id}')
        except Exception as exc:
            self._log_event('ERROR', f'Enter failed: {exc}')

    async def _exit(self, reason: str):
        if not self.position or not self.account_number:
            return
        pos         = self.position
        close_instr = 'SELL' if pos['side'] == 'LONG' else 'BUY'
        self._log_event('TRADE', f'EXIT {pos["side"]} {pos["symbol"]} — {reason}')
        try:
            if pos.get('order_id'):
                await asyncio.to_thread(cancel_order, self.account_number, str(pos['order_id']))
            await asyncio.to_thread(
                close_equity_position,
                self.account_number, pos['symbol'], close_instr, pos['quantity'],
            )
        except Exception as exc:
            self._log_event('ERROR', f'Exit failed: {exc}')
        self.position = None
        self.armed    = None

    async def _check_stop_filled(self):
        """Detect when the bracket (stop or T1) has been filled by checking
        Schwab positions. If the symbol is no longer held, the bracket exited."""
        if not self.position or not self.account_number:
            return
        try:
            positions = await asyncio.to_thread(get_positions, self.account_number)
            symbol    = self.position['symbol']
            side      = self.position['side']
            qty       = self.position['quantity']
            for pos in (positions or []):
                inst = pos.get('instrument', {})
                if inst.get('symbol') != symbol:
                    continue
                held = pos.get('longQuantity', 0) if side == 'LONG' else pos.get('shortQuantity', 0)
                if held >= qty:
                    return  # position still fully open
                # Partial or full fill of the bracket
                self._log_event('TRADE',
                    f'Bracket filled — held={held} vs entered={qty} — back to IDLE')
                self.position = None
                return
            # Symbol not in positions at all — bracket fully exited
            self._log_event('TRADE', 'Bracket exit confirmed (symbol gone from positions) — IDLE')
            self.position = None
        except Exception:
            pass


_equity_bot = EquityBot()


async def _equity_bot_loop():
    while True:
        try:
            await _equity_bot.tick()
        except Exception as exc:
            pass
        await asyncio.sleep(5)


# ── equity bot API endpoints ──────────────────────────────────────────────────

@app.get('/api/equity-bot/symbols')
async def api_equity_bot_symbols():
    equities = sorted(
        s['ticker'] for s in state.get('symbols', [])
        if not s.get('ticker', '').startswith('/')
    )
    return {'symbols': equities}


@app.get('/api/equity-bot/status')
async def api_equity_bot_status():
    return _equity_bot.status()


class EquityBotEnableRequest(BaseModel):
    symbol:   str
    model:    str   = 'CON'
    stop_pts: int   = 100
    quantity: int   = 1

@app.post('/api/equity-bot/enable')
async def api_equity_bot_enable(req: EquityBotEnableRequest):
    return _equity_bot.enable(req.symbol, req.model, req.stop_pts, req.quantity)

@app.post('/api/equity-bot/disable')
async def api_equity_bot_disable():
    return _equity_bot.disable()

@app.post('/api/equity-bot/close')
async def api_equity_bot_close():
    return await _equity_bot.emergency_close()


# ── manual order endpoints ────────────────────────────────────────────────────

_MANUAL_ALLOWED = {'/MES', '/MNQ', '/M2K', '/MYM', '/MGC'}

@app.get('/api/futures/quote')
async def api_futures_quote(asset: str):
    if asset not in _MANUAL_ALLOWED:
        return {'error': 'Asset not allowed'}
    symbol = front_month_code(asset)
    try:
        q = get_quotes([symbol])
        last = q.get(symbol, {}).get('last', 0)
        return {'asset': asset, 'symbol': symbol, 'last': last}
    except Exception as e:
        return {'error': str(e)}


class ManualTradeRequest(BaseModel):
    asset:      str
    side:       str
    quantity:   int   = 1
    stop_price: float = 0.0   # absolute stop level, computed by frontend

@app.post('/api/trade/manual')
async def api_manual_trade(req: ManualTradeRequest):
    if req.asset not in _MANUAL_ALLOWED:
        return {'error': f'Asset must be one of {sorted(_MANUAL_ALLOWED)}'}
    if req.side not in ('BUY', 'SELL'):
        return {'error': 'side must be BUY or SELL'}
    if req.quantity < 1 or req.quantity > 10:
        return {'error': 'quantity must be 1–10'}
    if req.stop_price <= 0:
        return {'error': 'stop_price must be a positive price level'}

    acct_list = await asyncio.to_thread(_trader_get, '/accounts/accountNumbers')
    if not acct_list:
        return {'error': 'No Schwab accounts linked'}
    acct = acct_list[0].get('hashValue')
    if not acct:
        return {'error': 'Could not resolve account hash'}

    symbol = front_month_code(req.asset)

    result = await asyncio.to_thread(
        place_futures_order,
        acct, symbol, req.side, req.quantity, req.stop_price,
    )

    if result is None:
        return {'error': 'Order placement failed — no response from Schwab'}
    if '_http_error' in result:
        return {'error': f"Schwab {result['_http_error']}: {result.get('_message', '')}"}

    return {
        'ok': True,
        'symbol': symbol,
        'side': req.side,
        'quantity': req.quantity,
        'stop_price': req.stop_price,
        'order_id': result.get('order_id'),
    }


# ── Swing scanner ─────────────────────────────────────────────────────────────

@app.get('/api/swing-scan')
async def api_swing_scan():
    """
    Return the latest persisted swing scan results from DB — always instant.
    Results are refreshed nightly by the background scheduler at 5:30 PM ET.
    Live price + pct_change overlaid from state['signals'] for VBH-tracked symbols;
    remaining symbols fetched in one Schwab batch call.
    """
    from scanner import load_swing_results
    rows = await asyncio.to_thread(load_swing_results)

    # Build price map: prev_close keyed by ticker (used to compute RTH % change)
    # prev_close = yesterday's RTH close from Schwab quotes (closePrice field)
    prev_close_map: dict[str, float] = {}
    for sig in state['signals']:
        ticker = sig.get('symbol')
        if ticker and ticker not in prev_close_map:
            last    = sig.get('last')
            net_chg = sig.get('net_change')
            pc      = sig.get('prev_close') or (last - net_chg if last and net_chg else None)
            if pc:
                prev_close_map[ticker] = pc

    # Find swing symbols not covered by state['signals'] — batch-fetch from Schwab
    missing = [r['ticker'] for r in rows if r.get('ticker') and r['ticker'] not in prev_close_map]
    CHUNK = 100
    for i in range(0, len(missing), CHUNK):
        chunk = missing[i:i + CHUNK]
        try:
            quotes = await asyncio.to_thread(get_quotes, chunk)
            for sym, q in quotes.items():
                close = q.get('close') or 0   # Schwab closePrice = yesterday's RTH close
                if close:
                    prev_close_map[sym] = close
        except Exception as e:
            log.warning('swing-scan Schwab batch quote error (chunk %d): %s', i // CHUNK, e)

    # Overlay RTH % change: (scan_price - prev_close) / prev_close
    # scan_price is the RTH close the scanner computed — never replaced with AH price.
    for row in rows:
        scan_p = row.get('price')          # RTH close stored by the scanner
        row['scan_price'] = scan_p
        pc = prev_close_map.get(row.get('ticker'))
        if scan_p and pc:
            row['pct_change'] = round((scan_p - pc) / pc * 100, 2)

    scanned_at = rows[0].get('scanned_at') if rows else None
    return {
        'rows'      : rows,
        'count'     : len(rows),
        'scanned_at': scanned_at,
    }


@app.post('/api/swing-scan/refresh')
async def api_swing_scan_refresh():
    """Trigger a full swing scan immediately and persist the results."""
    from scanner import scan_swing
    asyncio.create_task(asyncio.to_thread(scan_swing))
    return {'ok': True, 'message': 'Swing scan started — results will be ready in ~60s'}


@app.get('/api/lag-log')
async def api_lag_log():
    """Return the Laguerre signal log — all entries newest first."""
    from scanner import load_lag_signal_log
    rows = await asyncio.to_thread(load_lag_signal_log)
    return {'rows': rows, 'count': len(rows)}


def _validate_daily_job(run_date: str) -> None:
    """5:30 PM validator — confirms all 4:30 PM job steps completed and writes to job_run_log."""
    import logging as _log
    from db import get_db as _get_db
    db = _get_db()

    try:
        universe = db.table('ticker_universe').select('ticker', count='exact').execute()
        universe_count = universe.count or 0
    except Exception:
        universe_count = 646

    def _count(table: str, col: str, val: str) -> int:
        try:
            r = db.table(table).select('ticker', count='exact').eq(col, val).execute()
            return r.count or 0
        except Exception:
            return 0

    from datetime import date as _date
    import calendar as _cal
    today        = _date.fromisoformat(run_date)
    week_start   = (today - __import__('datetime').timedelta(days=today.weekday())).isoformat()
    month_start  = today.replace(day=1).isoformat()

    daily_count   = _count('ticker_candles_daily',   'bar_date', run_date)
    weekly_count  = _count('ticker_candles_weekly',  'bar_date', week_start)
    monthly_count = _count('ticker_candles_monthly', 'bar_date', month_start)

    # scan_count: rows updated today
    try:
        sc = db.table('swing_scan_results').select('ticker', count='exact') \
               .gte('scanned_at', f'{run_date}T00:00:00').execute()
        scan_count = sc.count or 0
    except Exception:
        scan_count = 0

    # lag signal count for today
    try:
        lc = db.table('lag_signal_log').select('ticker', count='exact') \
               .eq('signal_date', run_date).execute()
        lag_count = lc.count or 0
    except Exception:
        lag_count = 0

    threshold    = max(1, universe_count - 5)   # allow up to 5 failures
    daily_ok     = daily_count   >= threshold
    weekly_ok    = weekly_count  >= threshold
    monthly_ok   = monthly_count >= threshold
    bar_inject_ok = daily_ok                     # inject runs as part of daily
    scan_ok      = scan_count    >= threshold
    lag_ok       = lag_count     > 0

    all_ok = all([daily_ok, weekly_ok, monthly_ok, bar_inject_ok, scan_ok])

    notes_parts = []
    if not daily_ok:   notes_parts.append(f'daily short: {daily_count}/{universe_count}')
    if not weekly_ok:  notes_parts.append(f'weekly short: {weekly_count}/{universe_count}')
    if not monthly_ok: notes_parts.append(f'monthly short: {monthly_count}/{universe_count}')
    if not scan_ok:    notes_parts.append(f'scan short: {scan_count}/{universe_count}')
    if lag_ok:         notes_parts.append(f'{lag_count} lag signals logged')

    status = {
        'run_date':      run_date,
        'daily_ok':      daily_ok,
        'weekly_ok':     weekly_ok,
        'monthly_ok':    monthly_ok,
        'bar_inject_ok': bar_inject_ok,
        'scan_ok':       scan_ok,
        'lag_ok':        lag_ok,
        'daily_count':   daily_count,
        'weekly_count':  weekly_count,
        'monthly_count': monthly_count,
        'scan_count':    scan_count,
        'lag_count':     lag_count,
        'universe_count': universe_count,
        'notes':         ' | '.join(notes_parts) if notes_parts else 'All steps completed',
    }

    try:
        db.table('job_run_log').upsert(status, on_conflict='run_date').execute()
    except Exception as e:
        _log.warning('job_run_log write failed: %s', e)

    if all_ok:
        _log.info('✅ Daily job validated OK — daily=%d weekly=%d monthly=%d scan=%d lag=%d',
                  daily_count, weekly_count, monthly_count, scan_count, lag_count)
    else:
        _log.warning('⚠️  Daily job validation FAILED: %s', ' | '.join(notes_parts))


@app.get('/api/job-status')
async def api_job_status():
    """Return the latest job run log entry."""
    from db import get_db as _get_db
    db = _get_db()
    r = db.table('job_run_log').select('*').order('run_date', desc=True).limit(7).execute()
    return {'runs': r.data}
