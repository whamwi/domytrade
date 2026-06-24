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
calc_laguerre_signal(df)         Most recent Laguerre BUY/SELL crossover signal
                                 Returns dict: signal, entry, target, bars_ago
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


# ── Laguerre RSI Signal ───────────────────────────────────────────────────────

def calc_laguerre_signal(
    df:         pd.DataFrame,
    gamma:      float = 0.6,
    ob:         float = 0.8,
    os_:        float = 0.2,
    atr_len:    int   = 14,
    atr_factor: float = 3.0,
) -> dict:
    """Detect the most recent Laguerre RSI BUY/SELL crossover (TOS logic).

    Signal rules (translated from ThinkScript):
      BUY  — RSI crosses above 0.2 (oversold),
              OR RSI crosses above 0.8 while previously bearish (rsiu[1]==0)
      SELL — RSI crosses below 0.8 (overbought),
              OR RSI crosses below 0.2 while previously bullish (rsiu[1]==1)

    Target = entry ± atr_factor × ATR(atr_len)   [same as TOS default 3×ATR14]

    Returns
    -------
    dict with keys: signal ('BUY'|'SELL'|None), entry, target, bars_ago
    """
    close = df['Close']
    rsi_s = calc_laguerre(
        close, open_=df['Open'], high=df['High'], low=df['Low'], gamma=gamma
    ).values.astype(float)

    closes = close.values.astype(float)
    highs  = df['High'].values.astype(float)
    lows   = df['Low'].values.astype(float)
    prev_c = np.concatenate([[np.nan], closes[:-1]])

    tr  = np.maximum(highs - lows,
          np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
    atr = pd.Series(tr).rolling(atr_len).mean().values

    n    = len(rsi_s)
    rsiu = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        pr, cr = rsi_s[i - 1], rsi_s[i]
        if np.isnan(pr) or np.isnan(cr):
            rsiu[i] = rsiu[i - 1]
            continue
        x_above_ob = pr < ob <= cr
        x_above_os = pr < os_ <= cr
        x_below_ob = pr >= ob > cr
        if x_above_ob or x_above_os:
            rsiu[i] = 1
        elif rsiu[i - 1] == 1 and not x_below_ob and cr > os_:
            rsiu[i] = 1

    last_signal = last_entry = last_target = last_bars_ago = None
    for i in range(1, n):
        pr, cr    = rsi_s[i - 1], rsi_s[i]
        prev_rsiu = rsiu[i - 1]
        c, a      = closes[i], atr[i]
        if np.isnan(pr) or np.isnan(cr) or np.isnan(c):
            continue

        buy  = (pr < os_ <= cr) or (prev_rsiu == 0 and pr < ob <= cr)
        sell = (pr >= ob > cr)  or (prev_rsiu == 1 and pr >= os_ > cr)

        if buy or sell:
            last_signal   = 'BUY' if buy else 'SELL'
            last_entry    = round(float(c), 2)
            last_target   = round(float(c + atr_factor * a), 2) if buy and not np.isnan(a) \
                       else round(float(c - atr_factor * a), 2) if not np.isnan(a) else None
            last_bars_ago = n - 1 - i

    return {
        'signal'  : last_signal,
        'entry'   : last_entry,
        'target'  : last_target,
        'bars_ago': last_bars_ago,
    }
