"""
scanner.py — Carter's Squeeze Intel pipeline, Step 1: Scan + Enrich

Public API
----------
scan_ticker(ticker, fetch_monthly=True)  → dict with D/W/M squeeze results
scan_universe(symbols, fetch_monthly=False) → list of scan results

Monthly data source: Alpha Vantage TIME_SERIES_MONTHLY (full history)
Daily/Weekly: ticker_candles_daily table in Supabase (2-year backfill)
"""

import os, time, requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from db import get_db
from squeeze import _calc_squeeze

AV_KEY = os.environ.get('ALPHA_VANTAGE_API_KEY', '')
AV_RATE_DELAY = 12   # seconds between AV calls (free tier: 5 req/min)

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_daily_candles(ticker: str) -> pd.DataFrame:
    rows = (get_db()
            .table('ticker_candles_daily')
            .select('bar_date,open,high,low,close,volume')
            .eq('ticker', ticker.upper())
            .order('bar_date', desc=False)
            .execute().data)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['bar_date'] = pd.to_datetime(df['bar_date'])
    df = df.rename(columns={
        'bar_date': 'DateTime',
        'open': 'Open', 'high': 'High',
        'low':  'Low',  'close': 'Close', 'volume': 'Volume',
    })
    return df.set_index('DateTime').sort_index()


def agg_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample('W').agg(
        Open=('Open', 'first'), High=('High', 'max'),
        Low=('Low', 'min'),    Close=('Close', 'last'),
        Volume=('Volume', 'sum'),
    ).dropna()


def fetch_monthly_av(ticker: str) -> pd.DataFrame:
    """Fetch full monthly OHLCV history from Alpha Vantage."""
    if not AV_KEY:
        return pd.DataFrame()
    try:
        resp = requests.get(
            'https://www.alphavantage.co/query',
            params={
                'function': 'TIME_SERIES_MONTHLY',
                'symbol'  : ticker.upper(),
                'apikey'  : AV_KEY,
            },
            timeout=20,
        )
        if not resp.ok:
            return pd.DataFrame()
        data = resp.json().get('Monthly Time Series', {})
        if not data:
            return pd.DataFrame()
        rows = [
            {
                'DateTime': pd.to_datetime(dt_str),
                'Open'    : float(v['1. open']),
                'High'    : float(v['2. high']),
                'Low'     : float(v['3. low']),
                'Close'   : float(v['4. close']),
                'Volume'  : int(v['5. volume']),
            }
            for dt_str, v in data.items()
        ]
        df = pd.DataFrame(rows).sort_values('DateTime').reset_index(drop=True)
        return df.set_index('DateTime')
    except Exception:
        return pd.DataFrame()


# ── Squeeze runner ────────────────────────────────────────────────────────────

def _run_squeeze(df: pd.DataFrame, label: str) -> dict:
    """Reset index and run _calc_squeeze; tag result with timeframe label."""
    if df is None or df.empty:
        return {'tf': label, 'error': 'no data'}
    df_reset = df.reset_index()
    result = _calc_squeeze(df_reset)
    result['tf'] = label
    return result


def scan_ticker(ticker: str, fetch_monthly: bool = True) -> dict:
    """
    Run D / W / M squeeze on a single ticker.

    Returns
    -------
    {
      'ticker'  : str,
      'daily'   : squeeze result dict,
      'weekly'  : squeeze result dict,
      'monthly' : squeeze result dict | {'error': ...},
    }
    """
    ticker = ticker.upper().strip()
    daily = load_daily_candles(ticker)
    if daily.empty:
        return {'ticker': ticker, 'error': 'no daily data in DB'}

    weekly  = agg_weekly(daily)
    monthly = fetch_monthly_av(ticker) if fetch_monthly else pd.DataFrame()

    return {
        'ticker' : ticker,
        'daily'  : _run_squeeze(daily,   'D'),
        'weekly' : _run_squeeze(weekly,  'W'),
        'monthly': _run_squeeze(monthly, 'M'),
    }


# ── Universe scanner ──────────────────────────────────────────────────────────

def scan_universe(
    symbols: list[str],
    fetch_monthly: bool = False,
    av_rate_limit: bool = True,
) -> list[dict]:
    """
    Scan a list of symbols.  Monthly is off by default for full-universe runs
    (AV free tier = 25 calls/day); enable for filtered candidates only.

    Returns list of scan results, sorted by daily bars_in_squeeze descending.
    """
    results = []
    for i, sym in enumerate(symbols):
        r = scan_ticker(sym, fetch_monthly=fetch_monthly)
        results.append(r)
        if fetch_monthly and av_rate_limit and i < len(symbols) - 1:
            time.sleep(AV_RATE_DELAY)   # respect AV 5 req/min limit

    # Sort: in-squeeze stocks first, then by daily consecutive bar count
    def sort_key(r):
        d = r.get('daily', {})
        in_sq = d.get('sq_state', 'FIRED') != 'FIRED' and 'error' not in d
        bars  = d.get('bars_in_squeeze', 0)
        return (0 if in_sq else 1, -bars)

    results.sort(key=sort_key)
    return results


# ── Monthly candle loader ─────────────────────────────────────────────────────

def load_monthly_candles(ticker: str) -> pd.DataFrame:
    """Load monthly OHLCV from ticker_candles_monthly (backfilled via yfinance)."""
    rows = (get_db()
            .table('ticker_candles_monthly')
            .select('bar_date,open,high,low,close,volume')
            .eq('ticker', ticker.upper())
            .order('bar_date', desc=False)
            .execute().data)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['bar_date'] = pd.to_datetime(df['bar_date'])
    df = df.rename(columns={
        'bar_date': 'DateTime',
        'open': 'Open', 'high': 'High',
        'low': 'Low', 'close': 'Close', 'volume': 'Volume',
    })
    return df.set_index('DateTime').sort_index()


# ── VAW / VAM computation ─────────────────────────────────────────────────────

def _compute_va(daily_df: pd.DataFrame) -> tuple[float, float]:
    """
    Net Volume Average — weekly (VAW) and monthly (VAM).

    VA per bar = (Close - Midpoint) × Volume  where Midpoint = (High + Low) / 2
    VAW = sum of VA for bars in the current calendar week (Mon–last bar)
    VAM = sum of VA for bars in the current calendar month
    """
    df = daily_df.copy()
    df.index = pd.to_datetime(df.index)
    df['VA'] = (df['Close'] - (df['High'] + df['Low']) / 2) * df['Volume']

    last        = df.index[-1]
    week_start  = last - pd.Timedelta(days=last.dayofweek)   # Monday
    month_start = pd.Timestamp(last.year, last.month, 1)

    vaw = float(df.loc[df.index >= week_start,  'VA'].sum())
    vam = float(df.loc[df.index >= month_start, 'VA'].sum())
    return vaw, vam


def _va_badge(vaw: float, vam: float) -> str:
    if vaw > 0 and vam > 0: return 'ACCUM'
    if vaw < 0 and vam < 0: return 'DIST'
    if vaw > 0 and vam < 0: return 'CHURN↑'
    if vaw < 0 and vam > 0: return 'CHURN↓'
    return 'NEUTRAL'


# ── Swing-scan scorer ─────────────────────────────────────────────────────────

_SQ_IN_STATES = {'EXTRA_IN', 'EXTRA_OUT', 'ORIG_IN', 'ORIG_OUT', 'PRE_IN', 'PRE_OUT'}


def _score_swing(
    d_sq: dict, price: float, sma50: float,
    ema8: float, ema21: float, moxie_w: float, laguerre: float,
) -> tuple[int, int]:
    """
    Score long and short setups, 0-5 each (one point per indicator).

    Indicators:
      1. Squeeze D  — in squeeze + matching momo, OR fired <=3 bars with matching momo
      2. SMA50      — price above (long) / below (short)
      3. EMA stack  — EMA8 > EMA21 (long) / EMA8 < EMA21 (short)
      4. Moxie W    — weekly histogram positive (long) / negative (short)
      5. Laguerre   — > 0.5 (long) / < 0.5 (short)
    """
    d_state = d_sq.get('sq_state', 'FIRED')
    d_momo  = d_sq.get('momo_value') or 0.0
    d_fired = d_sq.get('bars_since_fired')
    in_sq   = d_state in _SQ_IN_STATES

    ls = ss = 0

    # 1 — Squeeze
    if (in_sq and d_momo >= 0) or (d_state == 'FIRED' and d_fired and d_fired <= 3 and d_momo > 0):
        ls += 1
    if (in_sq and d_momo <= 0) or (d_state == 'FIRED' and d_fired and d_fired <= 3 and d_momo < 0):
        ss += 1

    # 2 — SMA50
    if price > sma50: ls += 1
    else:             ss += 1

    # 3 — EMA stack
    if ema8 > ema21: ls += 1
    else:            ss += 1

    # 4 — Moxie (weekly)
    if moxie_w > 0: ls += 1
    else:           ss += 1

    # 5 — Laguerre RSI
    if laguerre > 0.5: ls += 1
    else:              ss += 1

    return ls, ss


def _scan_swing_ticker(ticker: str) -> dict | None:
    """Full swing analysis for one ticker. Returns None on insufficient data."""
    from indicators import calc_sma, calc_ema, calc_moxie, calc_laguerre

    daily = load_daily_candles(ticker)
    if daily.empty or len(daily) < 60:
        return None

    weekly  = agg_weekly(daily)
    monthly = load_monthly_candles(ticker)

    # ── Indicator values ──────────────────────────────────────────────────────
    price    = float(daily['Close'].iloc[-1])
    sma50    = float(calc_sma(daily['Close'], 50).iloc[-1])
    ema8     = float(calc_ema(daily['Close'],  8).iloc[-1])
    ema21    = float(calc_ema(daily['Close'], 21).iloc[-1])
    moxie_w  = float(calc_moxie(weekly['Close']).iloc[-1])
    laguerre = float(calc_laguerre(
        daily['Close'],
        open_=daily['Open'], high=daily['High'], low=daily['Low'],
    ).iloc[-1])

    # ── D / W / M squeeze ────────────────────────────────────────────────────
    d_sq = _run_squeeze(daily,   'D')
    w_sq = _run_squeeze(weekly,  'W')
    m_sq = _run_squeeze(monthly, 'M') if not monthly.empty else {'error': 'no data'}

    # ── Score ─────────────────────────────────────────────────────────────────
    ls, ss = _score_swing(d_sq, price, sma50, ema8, ema21, moxie_w, laguerre)
    if ls >= ss:
        direction, score = 'LONG',  ls
    else:
        direction, score = 'SHORT', ss

    # ── Multi-TF squeeze confirmation flags ───────────────────────────────────
    w_in = 'error' not in w_sq and w_sq.get('sq_state') in _SQ_IN_STATES
    m_in = 'error' not in m_sq and m_sq.get('sq_state') in _SQ_IN_STATES

    # ── VAW / VAM badge ───────────────────────────────────────────────────────
    vaw, vam = _compute_va(daily)
    badge    = _va_badge(vaw, vam)

    return {
        'ticker'       : ticker,
        'price'        : round(price, 2),
        'direction'    : direction,
        'score'        : score,
        'long_score'   : ls,
        'short_score'  : ss,
        # Daily squeeze
        'd_sq_state'   : d_sq.get('sq_state'),
        'd_sq_color'   : d_sq.get('sq_color'),
        'd_mo_state'   : d_sq.get('mo_state'),
        'd_mo_color'   : d_sq.get('mo_color'),
        'd_momo'       : d_sq.get('momo_value'),
        'd_bars_in_sq' : d_sq.get('bars_in_squeeze', 0),
        'd_bars_fired' : d_sq.get('bars_since_fired'),
        # Weekly squeeze
        'w_sq_state'   : w_sq.get('sq_state') if 'error' not in w_sq else None,
        'w_confirms'   : w_in,
        'w_bars_in_sq' : w_sq.get('bars_in_squeeze', 0) if 'error' not in w_sq else 0,
        # Monthly squeeze
        'm_sq_state'   : m_sq.get('sq_state') if 'error' not in m_sq else None,
        'm_confirms'   : m_in,
        # Indicator values
        'sma50'        : round(sma50, 2),
        'ema8'         : round(ema8, 2),
        'ema21'        : round(ema21, 2),
        'moxie_w'      : round(moxie_w, 4),
        'laguerre'     : round(laguerre, 4),
        # VAW / VAM (in millions)
        'vaw_m'        : round(vaw / 1e6, 2),
        'vam_m'        : round(vam / 1e6, 2),
        'va_badge'     : badge,
    }


def _persist_swing_results(rows: list[dict]) -> None:
    """Upsert swing scan results into swing_scan_results table."""
    from datetime import datetime, timezone
    db      = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_rows = [{**r, 'scanned_at': now_iso} for r in rows]
    db.table('swing_scan_results').upsert(
        upsert_rows, on_conflict='ticker',
    ).execute()


def load_swing_results() -> list[dict]:
    """Read persisted swing scan results from DB, sorted by score DESC -> d_bars_in_sq DESC."""
    resp = (get_db()
            .table('swing_scan_results')
            .select('*')
            .order('score', desc=True)
            .execute())
    rows = resp.data or []
    rows.sort(key=lambda r: (r['score'], r.get('d_bars_in_sq', 0)), reverse=True)
    return rows


def scan_swing(symbols: list[str] | None = None, persist: bool = True) -> list[dict]:
    """
    Full swing trade scan across the universe.

    Scores each symbol 0-5 (one point per indicator: Squeeze D, SMA50,
    EMA stack, Moxie W, Laguerre RSI) for LONG and SHORT separately.
    Direction = whichever scores higher.  Appends VAW/VAM badge.

    Sorted by score DESC -> d_bars_in_sq DESC.
    If persist=True (default), upserts results to swing_scan_results table.
    """
    db = get_db()
    if symbols is None:
        resp    = db.table('ticker_candles_daily').select('ticker').execute()
        symbols = sorted(set(r['ticker'] for r in resp.data))

    results = []
    for ticker in symbols:
        try:
            row = _scan_swing_ticker(ticker)
            if row:
                results.append(row)
        except Exception:
            pass

    results.sort(key=lambda r: (r['score'], r['d_bars_in_sq']), reverse=True)

    if persist and results:
        try:
            _persist_swing_results(results)
        except Exception:
            pass

    return results


# ── Grading helpers ───────────────────────────────────────────────────────────

IN_SQ_STATES = {'EXTRA_IN', 'EXTRA_OUT', 'ORIG_IN', 'ORIG_OUT', 'PRE_IN', 'PRE_OUT'}

def grade_ticker(scan: dict) -> dict:
    """
    Assign a Carter-style squeeze grade based on D/W/M squeeze agreement
    and consecutive bar counts.

    Grade logic
    -----------
    Daily bars_in_squeeze  ≥ 8  → +2 pts   (well coiled)
    Daily bars_in_squeeze  ≥ 4  → +1 pt    (forming)
    Weekly  in squeeze          → +2 pts
    Monthly in squeeze          → +2 pts
    Daily momentum positive     → +1 pt    (bull bias)
    Weekly momentum positive    → +1 pt

    Score → Grade
    8-9  → A+
    6-7  → A
    4-5  → B+
    2-3  → B
    0-1  → C
    """
    score = 0
    reasons = []

    d = scan.get('daily', {})
    w = scan.get('weekly', {})
    m = scan.get('monthly', {})

    # Daily squeeze duration
    d_bars = d.get('bars_in_squeeze', 0)
    d_in   = d.get('sq_state') in IN_SQ_STATES
    if d_in and d_bars >= 8:
        score += 2; reasons.append(f'D: {d_bars} bars in squeeze (+2)')
    elif d_in and d_bars >= 4:
        score += 1; reasons.append(f'D: {d_bars} bars in squeeze (+1)')
    elif d_in:
        reasons.append(f'D: {d_bars} bars in squeeze (too short, +0)')

    # Weekly squeeze
    w_in = w.get('sq_state') in IN_SQ_STATES
    if w_in:
        score += 2; reasons.append(f'W: in squeeze (+2)')

    # Monthly squeeze
    m_in = m.get('sq_state') in IN_SQ_STATES and 'error' not in m
    if m_in:
        score += 2; reasons.append(f'M: in squeeze (+2)')

    # Momentum bias
    if d.get('mo_state') in ('POS_UP', 'POS_DN'):
        score += 1; reasons.append('D: positive momentum (+1)')
    if w.get('mo_state') in ('POS_UP', 'POS_DN'):
        score += 1; reasons.append('W: positive momentum (+1)')

    # Grade
    if score >= 8:   grade = 'A+'
    elif score >= 6: grade = 'A'
    elif score >= 4: grade = 'B+'
    elif score >= 2: grade = 'B'
    else:            grade = 'C'

    return {
        'ticker' : scan['ticker'],
        'score'  : score,
        'grade'  : grade,
        'reasons': reasons,
        'd_state': d.get('sq_state', 'N/A'),
        'd_bars' : d_bars,
        'd_mo'   : d.get('mo_state', 'N/A'),
        'd_momo' : d.get('momo_value'),
        'w_state': w.get('sq_state', 'N/A'),
        'w_bars' : w.get('bars_in_squeeze', 0),
        'w_mo'   : w.get('mo_state', 'N/A'),
        'm_state': m.get('sq_state', 'N/A'),
        'm_bars' : m.get('bars_in_squeeze', 0),
        'm_mo'   : m.get('mo_state', 'N/A'),
    }
