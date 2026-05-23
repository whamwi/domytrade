"""
domytrade.app — FastAPI backend
Serves live VBH signals. Persists OHLC history and signals to Supabase.
"""
import asyncio, logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from schwab_client import get_quotes, get_candles, get_current_hour_ohlc
from vbh_engine import compute_stats, make_signal
from db import (get_active_symbols, upsert_ohlc, get_ohlc,
                upsert_vbh_stats, get_vbh_stats, insert_signals)

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)s  %(message)s')
log = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')

AGG_DAYS = 30
CON_DAYS = 90
STATS_REFRESH_HOURS = 24
SIGNAL_REFRESH_SECS = 60

# ── In-memory cache (rebuilt from DB on startup) ───────────────────────────────
state = {
    'symbols'          : [],   # [{id, ticker, schwab_symbol, asset_type}]
    'stats_agg'        : {},   # {symbol_id: {hour: (L1,L2,L3,L4)}}
    'stats_con'        : {},
    'signals'          : [],
    'last_stats_update': None,
    'last_signal_update': None,
    'status'           : 'starting',
}


# ── OHLC helpers ──────────────────────────────────────────────────────────────

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
            con_candles = await asyncio.to_thread(get_candles, api, CON_DAYS)
            rows = _candles_to_rows(sid, con_candles)
            if rows:
                upsert_ohlc(rows)

            # Compute stats directly from fresh candles (no DB round-trip needed)
            # AGG = last 30 days subset of the 90d fetch
            cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=AGG_DAYS)).timestamp() * 1000)
            agg_candles = [c for c in con_candles if c['datetime'] >= cutoff_ms]

            state['stats_agg'][sid] = compute_stats(agg_candles)
            state['stats_con'][sid] = compute_stats(con_candles)

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

    state['last_stats_update'] = datetime.now(ET).isoformat()
    log.info('Stats ready.')


async def refresh_signals():
    symbols = state['symbols']
    api_syms = [s['schwab_symbol'] for s in symbols]

    try:
        quotes = await asyncio.to_thread(get_quotes, api_syms)
    except Exception as e:
        log.warning('Quote fetch error: %s', e)
        return

    signal_hour = datetime.now(ET).replace(minute=0, second=0, microsecond=0)
    rows = []

    for sym in symbols:
        sid  = sym['id']
        tick = sym['ticker']
        api  = sym['schwab_symbol']

        q        = quotes.get(api, {})
        last     = q.get('last', 0)
        day_open = q.get('open', 0)
        if not last:
            continue

        try:
            ohlc = await asyncio.to_thread(get_current_hour_ohlc, api)
        except Exception:
            ohlc = None
        if not ohlc:
            ohlc = {'open': last, 'high': last, 'low': last, 'close': last, 'volume': 0}

        sigs = make_signal(
            tick, api, ohlc, last,
            state['stats_agg'].get(sid, {}),
            state['stats_con'].get(sid, {}),
        )
        if sigs:
            for s in sigs:
                s['symbol_id']   = sid
                s['signal_hour'] = signal_hour.isoformat()
                s['day_open']    = round(day_open, 4)
            rows.extend(sigs)
        await asyncio.sleep(0.1)

    rows.sort(key=lambda r: (r['side'] != 'LONG', -r['swing_pct']))
    state['signals'] = rows
    state['last_signal_update'] = datetime.now(ET).isoformat()
    state['status'] = 'live'
    log.info('Signals refreshed — %d rows', len(rows))

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


async def background_loop():
    state['symbols'] = get_active_symbols()
    log.info('Loaded %d symbols', len(state['symbols']))

    await compute_all_stats()
    await refresh_signals()

    while True:
        await asyncio.sleep(SIGNAL_REFRESH_SECS)
        await refresh_signals()

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


@app.get('/api/symbols')
def get_symbols_list():
    return {'symbols': [
        {'id': s['id'], 'ticker': s['ticker'], 'asset_type': s.get('asset_type', 'equity')}
        for s in state['symbols']
    ]}


@app.post('/api/refresh-stats')
async def force_refresh():
    asyncio.create_task(compute_all_stats())
    return {'message': 'Stats recomputation started'}
