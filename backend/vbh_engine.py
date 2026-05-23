"""
VBH engine — compute per-hour statistics and live signals.

Two models:
  aggressive   : 30-day lookback  → tighter boxes, more entries
  conservative : 90-day lookback  → wider boxes, higher conviction
"""
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')

# Minimum range samples before we emit a level (else 0.0)
MIN_SAMPLES = 5

# Tick size / tick value per symbol (for stop calculation, $50 risk default)
TICK_INFO: dict[str, tuple[float, float]] = {
    '/ES' : (0.25,  12.50),
    '/MES': (0.25,   1.25),
    '/NQ' : (0.25,   5.00),
    '/MNQ': (0.25,   0.50),
    '/RTY': (0.10,  10.00),
    '/M2K': (0.10,   1.00),
    '/YM' : (1.00,   5.00),
    '/MYM': (1.00,   0.50),
    '/CL' : (0.01,  10.00),
    '/MCL': (0.01,   1.00),
    '/GC' : (0.10,  10.00),
    '/MGC': (0.10,   1.00),
    '/SI' : (0.005, 25.00),
    '/HG' : (0.0005, 12.50),
    '/NG' : (0.001, 10.00),
    '/PL' : (0.10,  10.00),
    '/RB' : (0.0001,  4.20),
    '/ZB' : (1/32,  31.25),
    '/ZN' : (1/64,  15.625),
    '/ZC' : (0.25,  12.50),
    '/ZS' : (0.25,  12.50),
    '/BTC': (5.00,   5.00),
}
DEFAULT_TICK = (0.01, 1.00)   # equities


def _tick(api_symbol: str) -> tuple[float, float]:
    base = api_symbol.split(':')[0]   # strip exchange suffix
    return TICK_INFO.get(base, DEFAULT_TICK)


def _build_hourly(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df['dt'] = pd.to_datetime(df['datetime'], unit='ms', utc=True)
    df = df.set_index('dt')[['open', 'high', 'low', 'close', 'volume']]
    hourly = df.resample('1h').agg(
        open='first', high='max', low='min', close='last', volume='sum'
    ).dropna(subset=['open'])
    hourly.index = hourly.index.tz_convert(ET)
    hourly['hour_et'] = hourly.index.hour
    hourly['range']   = hourly['high'] - hourly['low']
    # drop closed-market bars (range < 10% of instrument median)
    med = hourly['range'].median()
    return hourly[hourly['range'] >= med * 0.10]


def compute_stats(candles: list[dict]) -> dict[int, tuple]:
    """
    Returns {hour: (L1, L2, L3, L4)} using mean±σ + 25th-pct for L4.
    Same formula as confirmed in original 2022 VBH study.
    """
    df = _build_hourly(candles)
    result = {}
    for h in range(24):
        vals = df[df['hour_et'] == h]['range'].dropna().values
        if len(vals) >= MIN_SAMPLES:
            mu  = float(np.mean(vals))
            sig = float(np.std(vals, ddof=1))
            l1  = max(0.0, mu - sig)
            l2  = mu
            l3  = mu + sig
            l4  = max(0.0, float(np.percentile(vals, 25)))
            result[h] = (l1, l2, l3, l4)
        else:
            result[h] = (0.0, 0.0, 0.0, 0.0)
    return result


def make_signal(
    symbol_display: str,
    api_symbol: str,
    current_hour_ohlc: dict,
    last_price: float,
    stats_agg: dict,
    stats_con: dict,
    dollar_risk: float = 50.0,
) -> dict | None:
    """
    Build a signal row for one symbol.
    Returns None if no VBH data for the current hour.
    """
    now_et = datetime.now(ET)
    h = now_et.hour

    agg = stats_agg.get(h, (0, 0, 0, 0))
    con = stats_con.get(h, (0, 0, 0, 0))

    if agg[2] == 0 and con[2] == 0:
        return None   # market closed this hour for this symbol

    h_open  = current_hour_ohlc.get('open',  last_price)
    h_high  = current_hour_ohlc.get('high',  last_price)
    h_low   = current_hour_ohlc.get('low',   last_price)
    current_range = h_high - h_low

    # Pick model: prefer aggressive if both available; flag both
    results = []
    for label, (l1, l2, l3, l4) in [('AGG', agg), ('CON', con)]:
        if l3 == 0:
            continue

        # ThinkScript box levels
        rhl  = h_low  + l1   # lower cyan (short trigger / long target zone)
        rlh  = h_high - l1   # upper cyan (long entry trigger)
        rhh  = h_low  + l3   # upper green box top
        rha  = h_low  + l2   # upper green box bottom
        rll  = h_high - l3   # lower red box bottom
        rla  = h_high - l2   # lower red box top
        long_t  = h_low  + l4  # long profit target (gray)
        short_t = h_high - l4  # short profit target (gray)

        # Stop calculation (ThinkScript formula)
        ts, tv = _tick(api_symbol)
        stop_translated = (dollar_risk / tv) * ts
        long_stop  = round((rlh - ((rlh - rll) + stop_translated)) / ts) * ts
        short_stop = round((rhl + ((rhh - rhl) + stop_translated)) / ts) * ts

        # Signal side: where is price relative to the box?
        if last_price <= rlh:
            side = 'LONG'
            entry  = rlh
            stop   = long_stop
            target = long_t
        elif last_price >= rhl:
            side = 'SHORT'
            entry  = rhl
            stop   = short_stop
            target = short_t
        else:
            # Price is inside the box — show both potential setups, bias toward long
            side   = 'LONG'
            entry  = rlh
            stop   = long_stop
            target = long_t

        # Daily swing: how big is the current range vs typical?
        typical = l3
        swing_pct   = round(current_range / typical * 100, 1) if typical else 0
        typical_pct = round(100.0, 1)

        results.append({
            'symbol'     : symbol_display,
            'api_symbol' : api_symbol,
            'side'       : side,
            'model'      : label,
            'entry'      : round(entry,  4),
            'stop'       : round(stop,   4),
            'target'     : round(target, 4),
            'last'       : round(last_price, 4),
            'hour_high'  : round(h_high, 4),
            'hour_low'   : round(h_low,  4),
            'swing_pct'  : swing_pct,
            'typical_pct': typical_pct,
            'typical_range': round(typical, 4),
            'current_range': round(current_range, 4),
            'hour_et'    : h,
            'l1': round(l1, 5), 'l2': round(l2, 5),
            'l3': round(l3, 5), 'l4': round(l4, 5),
        })

    return results or None
