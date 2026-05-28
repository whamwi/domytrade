"""
squeeze.py — 5-min SqueezePRO confirmation for /ES, /NQ, /YM, /RTY.

Ported from squeeze_live.py (no Schwab fetch functions, no GRaB).
Data source: ohlc_1min table via get_1min_range(sid, days=3).

Public API
----------
calc_squeeze_5min(rows_1min)           → sq_result dict
squeeze_confirms_signal(side, sq)      → (verdict, reason)
    verdict ∈ {'CONFIRMED', 'CAUTION', 'NEGATED', 'NEUTRAL'}
"""

import numpy as np
import pandas as pd

# ── Parameters — match TOS SqueezePRO defaults ────────────────────────────────
LENGTH      = 21
NUM_DEV_DN  = -2.0
NUM_DEV_UP  = +2.0
FACTOR_HIGH = 1.0
FACTOR_MID  = 1.5
FACTOR_LOW  = 2.0
TOLERANCE   = 0.05   # boundary tolerance to match TOS rounding


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df['Close'].shift(1)
    return pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)


def _inertia(series: pd.Series, length: int) -> pd.Series:
    """Linear-regression endpoint over a rolling window — TOS 'Inertia' function."""
    result = np.full(len(series), np.nan)
    vals   = series.values
    x      = np.arange(length)
    for i in range(length - 1, len(vals)):
        y = vals[i - length + 1 : i + 1]
        if np.isnan(y).any():
            continue
        coeffs    = np.polyfit(x, y, 1)
        result[i] = np.polyval(coeffs, length - 1)
    return pd.Series(result, index=series.index)


# ── 1-min → 5-min aggregation ─────────────────────────────────────────────────

def agg_1min_to_5min(rows: list[dict]) -> pd.DataFrame:
    """
    Aggregate DB ohlc_1min rows into 5-min OHLCV bars.

    rows = [{'bar_time': ISO str, 'open': float, 'high': float,
             'low': float, 'close': float, 'volume': int|None}, ...]
    Returns DataFrame with columns: DateTime, Open, High, Low, Close, Volume.
    Sorted ascending. Returns empty DataFrame if input is empty.
    """
    if not rows:
        return pd.DataFrame(columns=['DateTime', 'Open', 'High', 'Low', 'Close', 'Volume'])

    df1 = pd.DataFrame(rows)
    df1['DateTime'] = pd.to_datetime(df1['bar_time'], utc=True).dt.tz_localize(None)
    df1 = df1.rename(columns={
        'open': 'Open', 'high': 'High',
        'low':  'Low',  'close': 'Close', 'volume': 'Volume',
    })[['DateTime', 'Open', 'High', 'Low', 'Close', 'Volume']]
    df1 = df1.sort_values('DateTime').reset_index(drop=True)

    # Floor each 1-min bar to its 5-min bucket
    df1['bucket'] = df1['DateTime'].dt.floor('5min')
    agg = (df1.groupby('bucket')
              .agg(Open=('Open', 'first'), High=('High', 'max'),
                   Low=('Low', 'min'),    Close=('Close', 'last'),
                   Volume=('Volume', 'sum'))
              .reset_index()
              .rename(columns={'bucket': 'DateTime'}))
    return agg.sort_values('DateTime').reset_index(drop=True)


# ── Core SqueezePRO calculation ───────────────────────────────────────────────

def calc_squeeze_5min(rows_1min: list[dict]) -> dict:
    """
    Aggregate 1-min DB rows to 5-min bars and run SqueezePRO.

    Returns a result dict:
        sq_state    : str   — e.g. 'EXTRA_IN', 'ORIG_IN', 'PRE_IN', 'FIRED', …
        mo_state    : str   — 'POS_UP' | 'POS_DN' | 'NEG_DN' | 'NEG_UP' | 'UNKNOWN'
        momo_value  : float — raw inertia value
        just_fired  : bool  — squeeze released on this bar
        recently_fired: bool — released within last 5 bars
        bars_since_fired: int|None
        bars_used   : int   — 5-min bars used in calculation

    Returns {'error': reason} if insufficient data.
    """
    df = agg_1min_to_5min(rows_1min)
    return _calc_squeeze(df)


def _calc_squeeze(df: pd.DataFrame) -> dict:
    if len(df) < LENGTH + 2:
        return {'error': f'Need ≥{LENGTH + 2} bars, got {len(df)}'}

    close = df['Close']
    high  = df['High']
    low   = df['Low']

    # Bollinger Bands — EMA midline (TOS averageType=EXPONENTIAL)
    mid_bb   = _ema(close, LENGTH)
    sdev     = close.rolling(LENGTH).std(ddof=0)
    upper_bb = mid_bb + NUM_DEV_UP * sdev
    lower_bb = mid_bb + NUM_DEV_DN * sdev

    # Keltner Channels — EMA ATR
    atr           = _ema(_true_range(df), LENGTH)
    avg           = mid_bb
    upper_kc_high = avg + FACTOR_HIGH * atr
    lower_kc_high = avg - FACTOR_HIGH * atr
    upper_kc_mid  = avg + FACTOR_MID  * atr
    lower_kc_mid  = avg - FACTOR_MID  * atr
    upper_kc_low  = avg + FACTOR_LOW  * atr
    lower_kc_low  = avg - FACTOR_LOW  * atr

    # Squeeze states (with tolerance for TOS boundary matching)
    extra_sq = (lower_bb > lower_kc_high - TOLERANCE) & (upper_bb < upper_kc_high + TOLERANCE)
    orig_sq  = (lower_bb > lower_kc_mid  - TOLERANCE) & (upper_bb < upper_kc_mid  + TOLERANCE)
    pre_sq   = (lower_bb > lower_kc_low  - TOLERANCE) & (upper_bb < upper_kc_low  + TOLERANCE)

    lb_rising    = lower_bb > lower_bb.shift(1)
    extra_sq_in  = extra_sq &  lb_rising
    extra_sq_out = extra_sq & ~lb_rising
    orig_sq_in   = orig_sq  &  lb_rising
    orig_sq_out  = orig_sq  & ~lb_rising
    pre_sq_in    = pre_sq   &  lb_rising
    pre_sq_out   = pre_sq   & ~lb_rising

    # Momentum — Inertia of (close - midpoint) delta
    highest  = high.rolling(LENGTH).max()
    lowest   = low.rolling(LENGTH).min()
    K        = (highest + lowest) / 2 + _ema(close, LENGTH)
    raw_momo = close - K / 2
    momo     = _inertia(raw_momo, LENGTH)

    pos = momo >= 0
    neg = momo < 0
    up  = momo >= momo.shift(1)
    dn  = momo <  momo.shift(1)

    def b(s: pd.Series) -> bool:
        return bool(s.iloc[-1])

    def v(s: pd.Series) -> float:
        return float(s.iloc[-1])

    # ── Squeeze state (most restrictive level wins) ───────────────────────────
    if b(extra_sq_in):    sq_state = 'EXTRA_IN'
    elif b(extra_sq_out): sq_state = 'EXTRA_OUT'
    elif b(orig_sq_in):   sq_state = 'ORIG_IN'
    elif b(orig_sq_out):  sq_state = 'ORIG_OUT'
    elif b(pre_sq_in):    sq_state = 'PRE_IN'
    elif b(pre_sq_out):   sq_state = 'PRE_OUT'
    else:                 sq_state = 'FIRED'

    # ── Momentum state ────────────────────────────────────────────────────────
    if b(pos & up):   mo_state = 'POS_UP'
    elif b(pos & dn): mo_state = 'POS_DN'
    elif b(neg & dn): mo_state = 'NEG_DN'
    elif b(neg & up): mo_state = 'NEG_UP'
    else:             mo_state = 'UNKNOWN'

    # ── Recently fired detection ──────────────────────────────────────────────
    any_sq           = pre_sq | orig_sq | extra_sq
    prev_sq_val      = bool(any_sq.iloc[-2]) if len(df) > LENGTH + 2 else False
    just_fired       = prev_sq_val and sq_state == 'FIRED'
    recently_fired   = False
    bars_since_fired = None

    if sq_state == 'FIRED' and not just_fired:
        for lookback in range(2, min(6, len(df) - LENGTH)):
            if bool(any_sq.iloc[-lookback]):
                recently_fired   = True
                bars_since_fired = lookback - 1
                break

    return {
        'sq_state'        : sq_state,
        'mo_state'        : mo_state,
        'momo_value'      : round(v(momo), 4),
        'just_fired'      : just_fired,
        'recently_fired'  : recently_fired,
        'bars_since_fired': bars_since_fired,
        'bars_used'       : len(df),
    }


# ── Signal confirmation ───────────────────────────────────────────────────────

_IN_SQ = {'EXTRA_IN', 'EXTRA_OUT', 'ORIG_IN', 'ORIG_OUT', 'PRE_IN', 'PRE_OUT'}


def squeeze_confirms_signal(side: str, sq: dict) -> tuple[str, str]:
    """
    Map a SqueezePRO result to a verdict for an HBMR signal.

    Confirmation logic
    ------------------
    For a SHORT signal:
      • Momentum positive (POS_UP / POS_DN) → NEGATED or CAUTION
        (squeeze fired / coiling upward contradicts the short)
      • Momentum negative (NEG_DN / NEG_UP) → CONFIRMED or CAUTION
        (squeeze aligns with the short)

    For a LONG signal (symmetric):
      • Momentum negative (NEG_DN) → NEGATED
      • Momentum negative recovering (NEG_UP) → CAUTION
      • Momentum positive → CONFIRMED or CAUTION

    Parameters
    ----------
    side : 'LONG' | 'SHORT'
    sq   : dict from calc_squeeze_5min() — must not contain 'error' key

    Returns
    -------
    (verdict, reason)
      verdict ∈ {'CONFIRMED', 'CAUTION', 'NEGATED', 'NEUTRAL'}
    """
    if not sq or 'error' in sq:
        return ('NEUTRAL', 'no squeeze data')

    sq_st = sq['sq_state']
    mo    = sq['mo_state']
    in_sq = sq_st in _IN_SQ
    fired = sq_st == 'FIRED'

    if side == 'SHORT':
        # Bullish momentum — negate or caution the short
        if mo == 'POS_UP':
            if fired:
                return ('NEGATED', 'squeeze fired upward — skip short')
            if in_sq:
                return ('NEGATED', 'squeeze coiling up — skip short')
        if mo == 'POS_DN':
            return ('CAUTION', 'positive momentum fading — caution on short')
        # Bearish momentum — confirms the short
        if mo == 'NEG_DN':
            if fired:
                return ('CONFIRMED', 'squeeze fired downward — confirms short')
            if in_sq:
                return ('CONFIRMED', 'squeeze coiling downward — confirms short')
        if mo == 'NEG_UP':
            if fired:
                return ('CONFIRMED', 'squeeze fired downward — confirms short')
            if in_sq:
                return ('CAUTION', 'bear momentum recovering in squeeze')
        return ('NEUTRAL', f'sq={sq_st} mo={mo}')

    else:  # LONG
        # Bearish momentum — negate or caution the long
        if mo == 'NEG_DN':
            if fired:
                return ('NEGATED', 'squeeze fired downward — skip long')
            if in_sq:
                return ('NEGATED', 'squeeze coiling down — skip long')
        if mo == 'NEG_UP':
            return ('CAUTION', 'negative momentum recovering — caution on long')
        # Bullish momentum — confirms the long
        if mo == 'POS_UP':
            if fired:
                return ('CONFIRMED', 'squeeze fired upward — confirms long')
            if in_sq:
                return ('CONFIRMED', 'squeeze coiling upward — confirms long')
        if mo == 'POS_DN':
            if fired:
                return ('CONFIRMED', 'squeeze fired upward — confirms long')
            if in_sq:
                return ('CAUTION', 'bull momentum fading in squeeze')
        return ('NEUTRAL', f'sq={sq_st} mo={mo}')
