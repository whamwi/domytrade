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
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from schwab_client import (get_quotes, get_candles, get_daily_candles,
                           get_current_hour_ohlc, get_session_bars,
                           front_month_code, next_contract_month,
                           set_token_refresh_callback, _token_cache as _schwab_token_cache)
import vbh_engine
from vbh_engine import compute_stats, compute_stats_con, make_signal
from db import (get_active_symbols, upsert_ohlc, get_ohlc,
                upsert_vbh_stats, get_vbh_stats, insert_signals,
                upsert_1min, get_1min_today, get_1min_range,
                upsert_ticker_candles, get_ticker_candles, get_etf_holding_tickers,
                get_last_bar_times, delete_old_ticker_candles,
                upsert_daily_candles, get_daily_candles_db, get_daily_candles_batch,
                get_last_daily_bar_dates, delete_old_daily_candles,
                get_etf_holdings, set_etf_holdings,
                aggregate_1min_to_15min)

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)s  %(message)s')
log = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')

AGG_DAYS = 30
CON_DAYS = 90
STATS_REFRESH_HOURS = 24
SIGNAL_REFRESH_SECS = 60


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


# ── In-memory cache (rebuilt from DB on startup) ───────────────────────────────
state = {
    'symbols'          : [],   # [{id, ticker, schwab_symbol, asset_type}]
    'stats_agg'        : {},   # {symbol_id: {hour: (L1,L2,L3,L4)}}
    'stats_con'        : {},
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
    'signals'           : [],
    'last_stats_update': None,
    'last_signal_update': None,
    'status'           : 'starting',
    'active_contracts'  : {},   # {ticker: active_contract} e.g. {'/GC': '/GCQ26'}
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
            9 * 60 + 30 <= dt.hour * 60 + dt.minute < 16 * 60
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
        if 9 * 60 + 30 <= t < 16 * 60:
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

            # Compute stats directly from fresh candles (no DB round-trip needed)
            # AGG = last 30 days subset of the 90d fetch
            cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=AGG_DAYS)).timestamp() * 1000)
            agg_candles = [c for c in con_candles if c['datetime'] >= cutoff_ms]

            state['stats_agg'][sid]    = compute_stats(agg_candles, api)
            state['stats_con'][sid]    = compute_stats_con(con_candles, api)
            state['prev_close'][sid]   = _prev_rth_close(con_candles)
            state['market_bias'][sid]  = _rth_bias(con_candles)
            # Seed last_price with the absolute last candle close (any session).
            # For futures this captures the true last trade before the weekend
            # shutdown, not just the RTH close. refresh_signals() will overwrite
            # with the live quote once CME reopens.
            if con_candles:
                state['last_price'][sid] = round(con_candles[-1]['close'], 4)

            # Persist stats to DB
            stat_rows = []
            for h in range(24):
                for model, stats_dict in [('AGG', state['stats_agg'][sid]),
                                           ('CON', state['stats_con'][sid])]:
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

        # ── Running hourly high/low accumulator ───────────────────────────────
        # Accumulates every live quote sample since the current ET hour started.
        # This gives ~60s-resolution tracking of the hourly H/L, matching TOS
        # much more closely than using only the instantaneous last_price.
        if display_price:
            cur_hour = datetime.now(ET).hour
            if state['hourly_hour'].get(sid) != cur_hour:
                # New hour — reset accumulators
                state['hourly_hour'][sid] = cur_hour
                state['hourly_high'][sid] = display_price
                state['hourly_low'][sid]  = display_price
            else:
                state['hourly_high'][sid] = max(state['hourly_high'].get(sid, display_price), display_price)
                state['hourly_low'][sid]  = min(state['hourly_low'].get(sid,  display_price), display_price)

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
                today_rows = get_1min_today(sid)
                vp = _compute_vwap_poc(today_rows, MARKET_TICK.get(tick, 0.25))
                # Convert DB rows back to candle-like dicts for IB computation below
                fresh = [{'datetime': int(datetime.fromisoformat(r['bar_time']).timestamp() * 1000),
                           'high': r['high'], 'low': r['low']} for r in today_rows]
            except Exception:
                vp = {'vwap': None, 'poc': None}

            # Initial Balance: high/low of first 60 min of RTH (9:30–10:30 ET)
            try:
                ib_s = 9 * 60 + 30
                ib_e = 10 * 60 + 30
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
        if not last:
            continue

        # Build current-hour OHLC from 1-min bars in DB.
        # refresh_all_1min() runs before every refresh_signals() cycle and populates
        # 1-min bars for ALL futures — so hour_bars will be non-empty for every symbol.
        # Fold last_price into high/low to match TOS live-tick behaviour (the current
        # developing 1-min bar isn't closed yet so its high/low isn't in DB yet).
        # Fallback: if no bars yet (first run, cold start) anchor to last_price only.
        ohlc = None
        try:
            now_et_h   = datetime.now(ET)
            hour_floor = now_et_h.replace(minute=0, second=0, microsecond=0)
            min_bars   = get_1min_today(sid)
            hour_bars  = [
                b for b in min_bars
                if datetime.fromisoformat(b['bar_time']).astimezone(ET) >= hour_floor
            ]
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

        # Use accumulated hourly high/low — tracks every 60s quote sample since
        # the hour started, giving much closer match to TOS tick-by-tick tracking.
        acc_high = state['hourly_high'].get(sid, display_price)
        acc_low  = state['hourly_low'].get(sid,  display_price)

        if not ohlc:
            # Cold start — anchor to accumulated H/L (or live price if no accumulator yet)
            ohlc = {'open': display_price, 'high': acc_high, 'low': acc_low,
                    'close': display_price, 'volume': 0}
        else:
            # Merge closed 1-min bars with accumulated live H/L
            ohlc['high'] = max(ohlc['high'], acc_high)
            ohlc['low']  = min(ohlc['low'],  acc_low)

        # ── Daily bias: RTH open vs prev_settle ──────────────────────────
        rth_open_val   = state['rth_open'].get(sid, 0)
        prev_settl_val = state['prev_settle'].get(sid)
        if rth_open_val and prev_settl_val:
            if rth_open_val > prev_settl_val:
                state['daily_bias'][sid] = 'LONG'
            elif rth_open_val < prev_settl_val:
                state['daily_bias'][sid] = 'SHORT'
        bias_val = state['daily_bias'].get(sid)

        now_et     = datetime.now(ET)
        et_minute  = now_et.hour * 60 + now_et.minute

        sigs = make_signal(
            tick, api, ohlc, last,
            state['stats_agg'].get(sid, {}),
            state['stats_con'].get(sid, {}),
            daily_bias=bias_val,
            et_minute=et_minute,
        )
        if sigs:
            for s in sigs:
                s['symbol_id']   = sid
                s['signal_hour'] = signal_hour.isoformat()
                s['prev_close']  = round(prev_close, 4)
                s['net_change']  = round(net_chg_raw, 4)
                # Detect NEAR → ENTRY transition for one-shot beep on frontend
                sk = f"{sid}_{s['model']}"
                prev_st = state['prev_signal_state'].get(sk)
                s['entry_alert'] = (s['signal_state'] == 'ENTRY' and prev_st != 'ENTRY')
                state['prev_signal_state'][sk] = s['signal_state']
            rows.extend(sigs)
        await asyncio.sleep(0.1)

    rows.sort(key=lambda r: (r['side'] != 'LONG', -r['swing_pct']))
    state['signals'] = rows
    state['last_signal_update'] = datetime.now(ET).isoformat()
    state['status'] = 'live'
    log.info('Signals refreshed — %d rows', len(rows))

    # ── Persist snapshot to DB so next restart serves data instantly ──────────
    if rows:
        try:
            from db import cache_set
            cache_set('signals_snapshot', {
                'signals'     : rows,
                'last_updated': state['last_signal_update'],
            })
        except Exception as e:
            log.warning('Signal snapshot save error: %s', e)

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
            insert_signals(db_rows)
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


async def refresh_all_1min():
    """
    Fetch and store 1-min bars for EVERY active future every 60 seconds.

    Two-stage design:
      Stage 1 — fetch all symbols concurrently (one asyncio task per symbol).
      Stage 2 — write results to DB sequentially (avoids DB contention).

    This gives accurate hourly H/L for all futures (/GC, /ZB, /CL, /NG, /SI…)
    not just the 4 market futures, so the HBMR box is correct for every symbol.
    """
    futures_syms = [s for s in state['symbols'] if s['ticker'].startswith('/')]
    if not futures_syms:
        return

    # Stage 1 — concurrent fetches
    async def _fetch(sym: dict) -> tuple[int, list]:
        tick = sym['ticker']
        sid  = sym['id']
        try:
            api_sym = _active_contract(tick)
            candles = await asyncio.to_thread(get_session_bars, api_sym)
            return sid, (candles or [])
        except Exception as e:
            log.warning('1min fetch %s: %s', tick, e)
            return sid, []

    results = await asyncio.gather(*[_fetch(s) for s in futures_syms])

    # Stage 2 — sequential DB writes
    stored = 0
    for sid, candles in results:
        if candles:
            try:
                rows = _candles_to_1min_rows(sid, candles)
                upsert_1min(rows)
                stored += 1
            except Exception as e:
                log.warning('1min upsert sid=%s: %s', sid, e)

    log.info('1-min bars refreshed — %d/%d futures stored', stored, len(futures_syms))


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
    today_et   = datetime.now(ET).date()
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
            if chosen['open']:
                state['rth_open'][sid] = round(chosen['open'], 4)
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
        # Register callback so every future rotation is persisted automatically
        set_token_refresh_callback(
            lambda t: cache_set('schwab_refresh_token', {'token': t})
        )
    except Exception as e:
        log.warning('Schwab token cache load error: %s', e)

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

    await refresh_active_contracts()  # discover true active contracts FIRST (e.g. /GCQ26 not /GCM26)
    await compute_all_stats()
    await refresh_strip_opens()   # fetch true RTH 9:30 opens for Industries strip (incl. MAG10)
    await refresh_mag10_prices()  # prime MAG10 live prices before first request
    await refresh_all_1min()      # seed 1-min bars for ALL futures before first signal run
    await refresh_signals()

    # Kick off background tasks that don't block startup
    asyncio.create_task(refresh_etf_holdings())
    asyncio.create_task(refresh_ytd())
    asyncio.create_task(refresh_daily_candles(incremental=False))  # full 90-day batch

    last_strip_refresh      = time.time()
    last_holdings_refresh   = time.time()
    last_ytd_refresh        = time.time()
    last_candle_purge       = time.time()
    last_daily_refresh      = time.time()
    last_contract_refresh   = time.time()
    last_daily_close_run    = ''   # 'YYYY-MM-DD' of last 4:30 PM run
    last_1min_agg_run       = ''   # 'YYYY-MM-DD' of last 5:00 PM 1-min → 15-min aggregation
    last_vbh_update_run     = ''   # 'YYYY-MM-DD' of last 5:30 AM VBH table update

    while True:
        await asyncio.sleep(SIGNAL_REFRESH_SECS)   # 60s cadence
        # Stage 1: refresh 1-min bars for ALL futures concurrently, store in DB
        await refresh_all_1min()
        # Stage 2: compute signals — reads fresh 1-min bars from DB
        await refresh_signals()
        await refresh_mag10_prices()

        # Incremental 1-min candle update — runs every 60s during RTH
        asyncio.create_task(refresh_holding_candles())

        # refresh_strip_opens() fetches daily candles for MAG10 open prices.
        # rth_open for STRIP_ETFs comes from refresh_signals() live quotes — NOT here
        # (Schwab never returns in-progress daily bars so daily candles can't give today's open).
        if time.time() - last_strip_refresh >= STRIP_REFRESH_SECS:
            await refresh_strip_opens()
            last_strip_refresh = time.time()

        # Refresh ETF holdings once per day
        if time.time() - last_holdings_refresh >= HOLDINGS_REFRESH_SECS:
            asyncio.create_task(refresh_etf_holdings())
            _candle_tickers.clear()   # force ticker list reload after holdings refresh
            last_holdings_refresh = time.time()

        # Re-check active contracts once per hour (catches roll days automatically)
        if time.time() - last_contract_refresh >= CONTRACT_REFRESH_SECS:
            await refresh_active_contracts()
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
            last_daily_close_run = _today

        # 5:00 PM ET — compact 1-min bars older than 2 days into 15-min bars
        if _et_hhmm == 17 * 60 and last_1min_agg_run != _today:
            try:
                result = await asyncio.to_thread(aggregate_1min_to_15min, 2)
                log.info('1-min → 15-min aggregation: %d buckets written, %d rows deleted',
                         result['aggregated'], result['deleted'])
            except Exception as e:
                log.warning('1-min aggregation error: %s', e)
            last_1min_agg_run = _today

        # 5:30 AM ET — daily VBH table update (overnight session just closed)
        # Fetches new 30-min Schwab candles since last stored bar, recomputes
        # ATR means, upserts ohlc_hourly + vbh_stats, reloads engine cache.
        if _et_hhmm == 5 * 60 + 30 and last_vbh_update_run != _today:
            try:
                from vbh_updater import run_update
                result = await asyncio.to_thread(run_update)
                log.info('VBH scheduled update — ok=%s  failed=%s',
                         result['ok'], result['failed'])
            except Exception as e:
                log.warning('VBH scheduled update error: %s', e)
            last_vbh_update_run = _today

        if time.time() - last_daily_refresh >= 86400:   # full re-sync once per day
            asyncio.create_task(refresh_daily_candles(incremental=False))
            last_daily_refresh = time.time()

        if state['last_stats_update']:
            age_h = (datetime.now(ET) - datetime.fromisoformat(
                state['last_stats_update'])).total_seconds() / 3600
            if age_h >= STATS_REFRESH_HOURS:
                await compute_all_stats()


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_loop())
    yield
    task.cancel()


app = FastAPI(title='domytrade API', lifespan=lifespan)

app.add_middleware(CORSMiddleware,
    allow_origins=[
        'http://localhost:3000',
        'https://domytrade.app',
        'https://www.domytrade.app',
    ],
    allow_methods=['*'],
    allow_headers=['*'],
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


@app.get('/api/health')
def health():
    return {
        'status' : state['status'],
        'signals': len(state['signals']),
        'symbols': len(state['symbols']),
    }


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
      - Outside RTH: prev_close — frozen at 4:00 PM close, ignores extended hours"""
    _now_et = datetime.now(ET)
    _et_min = _now_et.hour * 60 + _now_et.minute
    # Simple weekday + time check — no strip_session_date dependency.
    # Schwab never returns in-progress daily candles so session_found was always False
    # which kept _is_rth=False all day, serving prev_close instead of live prices.
    _is_rth = _now_et.weekday() < 5 and (9 * 60 + 30) <= _et_min < 16 * 60

    ticker_map = {s['ticker']: s for s in state['symbols']}
    result = []
    for etf in STRIP_ETFS:
        tick = etf['ticker']
        sym  = ticker_map.get(tick)
        if not sym:
            continue
        sid      = sym['id']
        rth_open = state['rth_open'].get(sid, 0)
        # Live price during RTH; RTH close (ignoring extended hours) outside RTH
        current  = state['last_price'].get(sid, 0) if _is_rth else state['prev_close'].get(sid, 0)
        pct      = round((current - rth_open) / rth_open * 100, 2) if (rth_open and current) else 0.0
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

        is_rth      = wday < 5 and (9 * 60 + 30) <= t_min < 16 * 60
        is_on_wkday = wday < 5 and t_min < (9 * 60 + 30)
        is_sunday   = wday == 6   # CME reopens Sun 6 PM ET → belongs to Monday overnight

        if is_rth:
            rth_by_date.setdefault(d, []).append(c)
        elif is_on_wkday:
            on_by_date.setdefault(d, []).append(c)
        elif is_sunday:
            on_by_date.setdefault(d + timedelta(days=1), []).append(c)

    sorted_rth_dates = sorted(rth_by_date.keys(), reverse=True)

    # Session VPOC / VAH / VAL — most recent completed RTH session
    session_vpoc = None
    session_vah  = None
    session_val  = None
    prior_rth_dates_asc = [d for d in sorted_rth_dates if d < today]
    if prior_rth_dates_asc:
        prior_rth_bars = rth_by_date.get(prior_rth_dates_asc[0], [])
        session_va   = _compute_value_area(prior_rth_bars, tick)
        session_vpoc = session_va['poc']
        session_vah  = session_va['vah']
        session_val  = session_va['val']

    # Overnight VPOC — today's pre-market session (incl. Sunday night for Monday)
    overnight_vpoc = _compute_vpoc(on_by_date.get(today, []), tick)
    if not overnight_vpoc:
        # Fallback: most recent overnight with data
        for d in sorted(on_by_date.keys(), reverse=True):
            v = _compute_vpoc(on_by_date[d], tick)
            if v:
                overnight_vpoc = v
                break

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
        # Previous completed session
        prior_daily = [c for c in daily if _bar_date(c) < today]
        if prior_daily:
            prev        = prior_daily[-1]
            prev_high   = prev['high']
            prev_low    = prev['low']
            prev_close  = prev['close']
            daily_pivot = round((prev['high'] + prev['low'] + prev['close']) / 3, 4)

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

    # Initial Balance from today's raw_1min bars (9:30–10:30 ET)
    ib_s_levels = 9 * 60 + 30
    ib_e_levels = 10 * 60 + 30
    ib_bars_levels = []
    for c in raw_1min:
        dt_c   = datetime.fromtimestamp(c['datetime']/1000, tz=timezone.utc).astimezone(ET)
        t_min_c = dt_c.hour * 60 + dt_c.minute
        if ib_s_levels <= t_min_c < ib_e_levels:
            ib_bars_levels.append(c)
    ib_high = max(c['high'] for c in ib_bars_levels) if ib_bars_levels else None
    ib_low  = min(c['low']  for c in ib_bars_levels) if ib_bars_levels else None
    ib_complete = (now_et.weekday() < 5 and now_et.hour * 60 + now_et.minute >= ib_e_levels)

    return {
        'symbol':      symbol,
        'tick':        tick,
        'computed_at': now_et.strftime('%Y-%m-%d %H:%M ET'),
        'gap':         gap,
        'ib_high':     ib_high,
        'ib_low':      ib_low,
        'ib_complete': ib_complete,
        'levels': {
            'session_vpoc':   session_vpoc,
            'session_vah':    session_vah,
            'session_val':    session_val,
            'overnight_vpoc': overnight_vpoc,
            'mcvpoc_3day':    mcvpoc_3day,
            'daily_pivot':    daily_pivot,
            'weekly_pivot':   weekly_pivot,
            'weekly_open':    weekly_open,
            'ath_intraday':   ath_intraday,
            'swing_high':     swing_high,
            'swing_low':      swing_low,
            'prev_high':      prev_high,
            'prev_low':       prev_low,
            'prev_close':     prev_close,
            'vwap':           vwap,
        }
    }


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

    _check(levels.get('weekly_open'),  'Weekly Open')
    _check(levels.get('daily_pivot'),  'Daily Pivot')
    _check(levels.get('vwap'),         'VWAP')
    _check(levels.get('session_vpoc'), 'Session POC')

    direction = 'BULL' if score > 0 else ('BEAR' if score < 0 else 'NEUTRAL')
    return {'score': score, 'direction': direction, 'reasons': reasons}


def _agent_nearest_levels(price: float, levels: dict, naked_vpocs: list) -> dict:
    """Find 2 nearest levels above and 2 below current price."""
    label_map = {
        'session_vpoc'  : 'Session POC',
        'overnight_vpoc': 'Night POC',
        'mcvpoc_3day'   : '3-Day MCVPOC',
        'daily_pivot'   : 'Daily Pivot',
        'weekly_pivot'  : 'Weekly Pivot',
        'weekly_open'   : 'Weekly Open',
        'ath_intraday'  : 'ATH',
        'swing_high'    : 'Swing High',
        'swing_low'     : 'Swing Low',
        'prev_high'     : 'Prior High',
        'prev_low'      : 'Prior Low',
        'prev_close'    : 'Prior Close',
        'vwap'          : 'VWAP',
    }
    all_levels = []
    for key, val in levels.items():
        if val is None:
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
            is_rth      = wday < 5 and (9*60+30) <= t_min < 16*60
            is_on_wkday = wday < 5 and t_min < (9*60+30)
            is_sunday   = wday == 6
            if is_rth:
                rth_by_date.setdefault(d, []).append(c)
            elif is_on_wkday:
                on_by_date.setdefault(d, []).append(c)
            elif is_sunday:
                on_by_date.setdefault(d + timedelta(days=1), []).append(c)

        sorted_rth = sorted(rth_by_date.keys(), reverse=True)

        prior_rth_dates_agent = [d for d in sorted_rth if d < today]
        if prior_rth_dates_agent:
            _prior_agent_bars = rth_by_date.get(prior_rth_dates_agent[0], [])
            _session_va       = _compute_value_area(_prior_agent_bars, tick)
            session_vpoc      = _session_va['poc']
            session_vah       = _session_va['vah']
            session_val       = _session_va['val']
        else:
            session_vpoc = session_vah = session_val = None
        overnight_vpoc = _compute_vpoc(on_by_date.get(today, []), tick) if on_by_date.get(today) else None

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
            'session_vpoc': session_vpoc, 'session_vah': session_vah, 'session_val': session_val,
            'overnight_vpoc': overnight_vpoc,
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


def _build_claude_prompt(symbols_data: list[dict]) -> str:
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

    ib_complete = wday < 5 and t_min >= 10*60+30   # after 10:30 ET

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

    return '\n'.join(lines)


# System prompt — concise. Market Profile knowledge is encoded in _build_claude_prompt data.
# Prompt caching not used for narrative: calls are 15 min apart, Anthropic cache TTL is 5 min
# → cache always cold → write fee (125%) on every call with no read savings.
# Caching will be added to the per-user chat feature where calls cluster within 5 min.
_MP_SYSTEM_PROMPT = (
    'You are a concise futures trading assistant with expertise in Market Profile theory. '
    'Analyze the provided data — Value Area (VAH/VAL/POC), Initial Balance, gap, open type, '
    'naked POCs — and give brief actionable recommendations. '
    '2-3 sentences per symbol: open type assessment, key level to watch, one clear action.'
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

    # Fetch all 5 symbols concurrently
    results = await asyncio.gather(*[_fetch_agent_symbol_data(s) for s in AGENT_FUTURES])
    valid   = [r for r in results if r is not None]

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
                prompt = _build_claude_prompt(valid)
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
    """Fetch Asian index daily close % change via yfinance. Returns [] on failure."""
    import yfinance as yf
    syms = [x['symbol'] for x in ASIAN_INDICES]
    try:
        raw    = yf.download(syms, period='5d', interval='1d',
                             progress=False, auto_adjust=True,
                             group_by='column', threads=True)
        closes = raw['Close'] if 'Close' in raw.columns.get_level_values(0) else raw
        result = []
        for item in ASIAN_INDICES:
            try:
                col = closes[item['symbol']].dropna()
                if len(col) >= 2:
                    prev = float(col.iloc[-2])
                    last = float(col.iloc[-1])
                    result.append({
                        'name'      : item['name'],
                        'region'    : item['region'],
                        'close'     : round(last, 2),
                        'change_pct': round((last - prev) / prev * 100, 2),
                    })
            except Exception:
                pass
        return result
    except Exception as e:
        log.warning('Asia yfinance fetch error: %s', e)
        return []


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
      • FX  — live from Schwab quotes (refreshed every 15 min)
      • Asia — yfinance daily close; written to DB on success; DB is the fallback
    """
    from db import cache_get, cache_set

    # ── FX from Schwab ─────────────────────────────────────────────────────────
    fx = await asyncio.to_thread(_fetch_fx_schwab)

    # ── Asian indices from yfinance (once a day is enough) ────────────────────
    today_str = datetime.now(ET).date().isoformat()
    asia: list[dict] = []

    # Check DB cache first — only re-fetch yfinance if date has changed
    cached = await asyncio.to_thread(cache_get, 'global_markets_asia')
    if cached and cached.get('date') == today_str and cached.get('asia'):
        asia = cached['asia']
        log.debug('Asia data served from DB cache for %s', today_str)
    else:
        # Try yfinance; write to DB on success; fall back to DB on failure
        fresh = await asyncio.to_thread(_fetch_asia_yfinance)
        if fresh:
            asia = fresh
            await asyncio.to_thread(cache_set, 'global_markets_asia', {
                'asia': asia,
                'date': today_str,
            })
            log.info('Asia data refreshed via yfinance (%d indices) and saved to DB', len(asia))
        elif cached and cached.get('asia'):
            asia = cached['asia']
            log.warning('yfinance rate-limited — serving Asia data from DB cache (date: %s)',
                        cached.get('date', '?'))
        else:
            log.warning('No Asia data available: yfinance failed and DB cache is empty')

    return {'asia': asia, 'fx': fx}


@app.get('/api/global-markets')
async def get_global_markets():
    """Asian market daily close change % and FX risk-on/off pairs.
    Asia: yfinance, cached in DB daily. FX: live from Schwab, cached 15 min in memory."""
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

    return {
        'symbol'       : symbol,
        'tick'         : tick,
        'bars_fetched' : len(raw_1min),
        'first_bar'    : first_bar_dt.strftime('%Y-%m-%d %H:%M ET'),
        'last_bar'     : last_bar_dt.strftime('%Y-%m-%d %H:%M ET'),
        'sessions'     : sessions,
        'naked_count'  : len(naked_vpocs),
        'naked_vpocs'  : [{'date': s['date'], 'vpoc': s['vpoc']} for s in naked_vpocs],
    }
