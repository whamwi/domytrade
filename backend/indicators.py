"""
indicators.py — Standalone technical indicators for the swing scanner.

Each function takes a pandas Series (close prices, sorted ascending) and
returns a pandas Series aligned to the same index.

Functions
---------
calc_sma(close, period)          Simple Moving Average
calc_ema(close, period)          Exponential Moving Average
calc_moxie(close)                Moxie = MACD histogram × 3
                                 Caller must pass the NEXT HIGHER timeframe close
                                 (e.g. weekly close when scanning daily)
calc_laguerre(close, gamma)      Laguerre RSI  (0–1 scale)
"""

import numpy as np
import pandas as pd


# ── SMA ───────────────────────────────────────────────────────────────────────

def calc_sma(close: pd.Series, period: int = 50) -> pd.Series:
    """Simple Moving Average.

    Returns NaN for the first (period-1) bars.
    """
    return close.rolling(period).mean()


# ── EMA ───────────────────────────────────────────────────────────────────────

def calc_ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average — matches TOS ExpAverage()."""
    return close.ewm(span=period, adjust=False).mean()


# ── Moxie ─────────────────────────────────────────────────────────────────────

def calc_moxie(close: pd.Series) -> pd.Series:
    """Moxie Momentum indicator (Watkins).

    Formula (TOS):
        vc1  = EMA(close, 12) - EMA(close, 26)   ← MACD line
        va1  = EMA(vc1, 9)                        ← Signal line
        data = (vc1 - va1) * 3                    ← Histogram × 3

    IMPORTANT — timeframe:
        On a Daily chart TOS uses the NEXT HIGHER timeframe (weekly close).
        Pass resampled weekly close when computing for a daily scanner.

    Returns:
        Positive = bullish momentum; Negative = bearish.
        Colour rule (matches TOS): green when rising, red when falling.
    """
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return (macd - signal) * 3


# ── Laguerre RSI ──────────────────────────────────────────────────────────────

def calc_laguerre(
    close: pd.Series,
    open_: "pd.Series | None" = None,
    high:  "pd.Series | None" = None,
    low:   "pd.Series | None" = None,
    gamma: float = 0.6,
) -> pd.Series:
    """Laguerre RSI — matches TOS default (candle_hybrid, gamma=0.6).

    TOS uses a hybrid candle as the filter input (not raw close):
        o = (open + prev_close) / 2
        h = max(high, prev_close)
        l = min(low,  prev_close)
        c = (o + h + l + close) / 4

    Pass open_, high, low for accurate TOS matching.
    Falls back to raw close if OHLC not provided.

    Output range 0.0 – 1.0:  < 0.2 oversold  /  > 0.8 overbought
    """
    if open_ is not None and high is not None and low is not None:
        prev_close = close.shift(1)
        o = (open_ + prev_close) / 2
        h = pd.concat([high, prev_close], axis=1).max(axis=1)
        l = pd.concat([low,  prev_close], axis=1).min(axis=1)
        c = (o + h + l + close) / 4
    else:
        c = close

    prices = c.values.astype(float)
    n      = len(prices)
    out    = np.full(n, np.nan)

    L0 = L1 = L2 = L3 = 0.0
    g  = gamma
    g1 = 1.0 - gamma

    for i in range(n):
        p = prices[i]
        if np.isnan(p):
            continue

        L0_new = g1 * p  + g * L0
        L1_new = -g * L0_new + L0 + g * L1
        L2_new = -g * L1_new + L1 + g * L2
        L3_new = -g * L2_new + L2 + g * L3

        L0, L1, L2, L3 = L0_new, L1_new, L2_new, L3_new

        CU = max(L0 - L1, 0) + max(L1 - L2, 0) + max(L2 - L3, 0)
        CD = max(L1 - L0, 0) + max(L2 - L1, 0) + max(L3 - L2, 0)

        denom = CU + CD
        out[i] = CU / denom if denom != 0 else 0.0

    return pd.Series(out, index=close.index)
