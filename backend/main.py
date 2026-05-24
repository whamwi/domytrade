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

from schwab_client import get_quotes, get_candles, get_current_hour_ohlc, get_session_bars, front_month_code
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
    'prev_close'       : {},   # {symbol_id: float}  — last RTH close from candles
    'market_bias'      : {},   # {symbol_id: {bias, pts, rth_open, prev_close}}
    'last_price'       : {},   # {symbol_id: float}  — latest price (live quote or prev_close fallback)
    'net_change'       : {},   # {symbol_id: float}  — Schwab net_change (vs CME settlement / prev close)
    'signals'          : [],
    'last_stats_update': None,
    'last_signal_update': None,
    'status'           : 'starting',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prev_rth_close(candles: list[dict]) -> float:
    """Return the close of the most recent completed RTH bar (weekday 9:30–16:00 ET)."""
    if not candles:
        return 0.0
    rth = [
        (c['datetime'], c['close'])
        for c in candles
        if (lambda dt: dt.weekday() < 5 and 9 * 60 + 30 <= dt.hour * 60 + dt.minute < 16 * 60)(
            datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc).astimezone(ET)
        )
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
            # Futures price history requires a specific contract month symbol (e.g. /ESM26)
            # Continuous symbols like /ES:XCME are rejected by Schwab's price history API
            candle_sym = front_month_code(tick) if tick.startswith('/') else api
            log.info('  %-8s  fetching candles as %s', tick, candle_sym)
            con_candles = await asyncio.to_thread(get_candles, candle_sym, CON_DAYS)
            rows = _candles_to_rows(sid, con_candles)
            if rows:
                upsert_ohlc(rows)

            # Compute stats directly from fresh candles (no DB round-trip needed)
            # AGG = last 30 days subset of the 90d fetch
            cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=AGG_DAYS)).timestamp() * 1000)
            agg_candles = [c for c in con_candles if c['datetime'] >= cutoff_ms]

            state['stats_agg'][sid]    = compute_stats(agg_candles)
            state['stats_con'][sid]    = compute_stats(con_candles)
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

    state['last_stats_update'] = datetime.now(ET).isoformat()
    log.info('Stats ready.')


async def refresh_signals():
    symbols = state['symbols']
    # For futures, use the specific contract month symbol for quotes (e.g. /ESM26)
    # — the continuous symbol (/ES:XCME) may return zero net_change when CME is closed.
    # For equities, use the schwab_symbol as-is.
    def _quote_sym(s):
        tick = s['ticker']
        return front_month_code(tick) if tick.startswith('/') else s['schwab_symbol']

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
            # prev_settle is always accurate for futures (official CME settlement)
            prev_settle = round(q_last - net_chg, 2) if net_chg else 0
            if q_open > 0 and prev_settle > 0:
                pts = round(q_open - prev_settle, 2)   # open vs prev settlement
            elif prev_settle > 0:
                pts = round(net_chg, 2)                 # off-hours: use running change
            else:
                pts = 0.0
            if abs(pts) <= NEUTRAL_BAND:
                mbias = 'NEUTRAL'
            elif pts > 0:
                mbias = 'BULL'
            else:
                mbias = 'BEAR'
            # Fetch today's session bars for VWAP / POC
            try:
                session_sym  = front_month_code(tick)
                session_bars = await asyncio.to_thread(get_session_bars, session_sym)
                vp = _compute_vwap_poc(session_bars, MARKET_TICK.get(tick, 0.25))
            except Exception:
                vp = {'vwap': None, 'poc': None}

            state['market_bias'][sid] = {
                'bias': mbias, 'pts': pts,
                'rth_open': q_open, 'prev_close': prev_settle,
                'vwap': vp['vwap'], 'poc': vp['poc'],
            }
        if not last:
            continue

        try:
            ohlc_sym = front_month_code(tick) if tick.startswith('/') else api
            ohlc = await asyncio.to_thread(get_current_hour_ohlc, ohlc_sym)
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
                s['prev_close']  = round(prev_close, 4)
                s['net_change']  = round(net_chg_raw, 4)
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
        })
    # Fixed display order
    order = ['/ES', '/NQ', '/YM', '/RTY']
    result.sort(key=lambda r: order.index(r['symbol']) if r['symbol'] in order else 99)
    return {'markets': result}


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


@app.post('/api/refresh-stats')
async def force_refresh():
    asyncio.create_task(compute_all_stats())
    return {'message': 'Stats recomputation started'}
