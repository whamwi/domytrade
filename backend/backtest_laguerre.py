#!/usr/bin/env python3
"""
backtest_laguerre.py — Laguerre RSI conditional backtest

Tests 4 squeeze pre-conditions × 6 gammas to find the best combination.

Conditions tested (for each Laguerre signal):
  NONE    — no filter (baseline)
  SAME    — Laguerre fires on exact squeeze-fire bar (sq_fired=True + matching momo)
  NEARBY  — Laguerre fires within ±3 bars of a squeeze fire
  IN_SQ   — Laguerre fires while any squeeze is active + momo aligns

Stop   = entry ∓ 1×ATR14   (= (target - entry) / 3)
Target = entry ± 3×ATR14
Graded: subsequent bars until HIT_TARGET, HIT_STOP, or MAX_BARS timeout.

Usage:
    python3 backtest_laguerre.py                       # all conditions × all gammas
    python3 backtest_laguerre.py --cond SAME NEARBY    # specific conditions
    python3 backtest_laguerre.py --gamma 0.6 0.8       # specific gammas
    python3 backtest_laguerre.py --tickers AAPL MSFT   # subset
"""
import sys, os, argparse, time
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from db import get_db
from indicators import calc_laguerre

GAMMAS     = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
ATR_LEN    = 14
ATR_FACTOR = 3.0
OB, OS     = 0.8, 0.2
MAX_BARS   = 90
NEARBY_WIN = 3        # bars either side for NEARBY condition


# ── Squeeze state series (vectorized via calc_big3) ───────────────────────────

def squeeze_series(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns three boolean arrays aligned to df's index:
      sq_fired  — bar where squeeze just released (GREEN dot appears)
      in_sq     — bar is inside any squeeze (pre/orig/extra)
      momo_pos  — momentum is positive (POS_UP or POS_DN)
    """
    from big3_squeeze import calc_big3
    sq = calc_big3(df)
    if sq.empty:
        n = len(df)
        return np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool)

    # sq_fired: the release bar (sq_state FIRED is a single transition bar)
    fired = sq['sq_state'].values == 'FIRED'
    # Build a "just fired" marker: first bar of a FIRED run
    fired_arr = np.zeros(len(sq), bool)
    for i in range(1, len(sq)):
        if sq['sq_state'].iloc[i] == 'FIRED' and sq['sq_state'].iloc[i-1] != 'FIRED':
            fired_arr[i] = True

    in_sq_arr = sq['sq_state'].isin(
        ['EXTRA_IN','EXTRA_OUT','ORIG_IN','ORIG_OUT','PRE_IN','PRE_OUT']
    ).values

    pos_mo = sq['mo_state'].isin(['POS_UP', 'POS_DN']).values

    return fired_arr, in_sq_arr, pos_mo


# ── Extract all historical Laguerre signals ───────────────────────────────────

def all_signals(df: pd.DataFrame, gamma: float) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns:
      events  — list of signal dicts
      fired   — bool array: just-fired bar
      in_sq   — bool array: in any squeeze
      pos_mo  — bool array: positive momo
    """
    close  = df['Close']
    rsi_s  = calc_laguerre(close, open_=df['Open'], high=df['High'], low=df['Low'],
                           gamma=gamma).values.astype(float)

    closes = close.values.astype(float)
    opens  = df['Open'].values.astype(float)
    highs  = df['High'].values.astype(float)
    lows   = df['Low'].values.astype(float)
    dates  = df.index.to_numpy()
    prev_c = np.concatenate([[np.nan], closes[:-1]])

    o_h     = (opens + prev_c) / 2
    h_h     = np.maximum(highs, prev_c)
    l_h     = np.minimum(lows,  prev_c)
    c_h     = (o_h + h_h + l_h + closes) / 4
    prev_ch = np.concatenate([[np.nan], c_h[:-1]])
    tr      = np.maximum(h_h - l_h,
              np.maximum(np.abs(h_h - prev_ch), np.abs(l_h - prev_ch)))
    atr     = pd.Series(tr).rolling(ATR_LEN).mean().values

    n    = len(rsi_s)
    rsiu = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        pr, cr = rsi_s[i-1], rsi_s[i]
        if np.isnan(pr) or np.isnan(cr):
            rsiu[i] = rsiu[i-1]; continue
        x_above_ob = pr < OB <= cr
        x_above_os = pr < OS <= cr
        x_below_ob = pr >= OB > cr
        if x_above_ob or x_above_os:
            rsiu[i] = 1
        elif rsiu[i-1] == 1 and not x_below_ob and cr > OS:
            rsiu[i] = 1

    events = []
    for i in range(1, n):
        pr, cr    = rsi_s[i-1], rsi_s[i]
        prev_rsiu = rsiu[i-1]
        c, a      = closes[i], atr[i]
        if np.isnan(pr) or np.isnan(cr) or np.isnan(c) or np.isnan(a):
            continue
        buy  = (pr < OS <= cr) or (prev_rsiu == 0 and pr < OB <= cr)
        sell = (pr >= OB > cr)  or (prev_rsiu == 1 and pr >= OS > cr)
        if not (buy or sell):
            continue
        sig    = 'BUY' if buy else 'SELL'
        entry  = float(c)
        dist   = ATR_FACTOR * float(a)
        target = round(entry + dist, 4) if buy else round(entry - dist, 4)
        stop   = round(entry - dist / ATR_FACTOR, 4) if buy \
                 else round(entry + dist / ATR_FACTOR, 4)
        events.append({
            'idx'   : i,
            'date'  : str(dates[i])[:10],
            'signal': sig,
            'entry' : entry,
            'target': target,
            'stop'  : stop,
        })

    fired, in_sq, pos_mo = squeeze_series(df)
    return events, fired, in_sq, pos_mo


# ── Condition filter ──────────────────────────────────────────────────────────

def passes_condition(ev: dict, condition: str,
                     fired: np.ndarray, in_sq: np.ndarray,
                     pos_mo: np.ndarray, n: int) -> bool:
    if condition == 'NONE':
        return True

    i   = ev['idx']
    buy = ev['signal'] == 'BUY'

    if condition == 'SAME':
        # Laguerre fires on exact squeeze-release bar with matching momo
        return bool(fired[i]) and (pos_mo[i] if buy else not pos_mo[i])

    if condition == 'NEARBY':
        # Laguerre fires within NEARBY_WIN bars of a squeeze release, momo aligns
        lo = max(0, i - NEARBY_WIN)
        hi = min(n - 1, i + NEARBY_WIN)
        fire_nearby = any(fired[lo:hi+1])
        return fire_nearby and (pos_mo[i] if buy else not pos_mo[i])

    if condition == 'IN_SQ':
        # Laguerre fires while in squeeze with aligning momo
        return bool(in_sq[i]) and (pos_mo[i] if buy else not pos_mo[i])

    return True


# ── Grade one signal ──────────────────────────────────────────────────────────

def grade(ev: dict, highs: np.ndarray, lows: np.ndarray,
          closes: np.ndarray, n: int) -> dict:
    i   = ev['idx']
    sig = ev['signal']
    tgt = ev['target']
    stp = ev['stop']

    for j in range(i + 1, min(i + 1 + MAX_BARS, n)):
        h, l = highs[j], lows[j]
        hit_stop   = (l <= stp) if sig == 'BUY' else (h >= stp)
        hit_target = (h >= tgt) if sig == 'BUY' else (l <= tgt)
        if hit_stop and hit_target:
            hit_target = False   # stop assumed first (conservative)
        if hit_stop:
            pnl = (stp - ev['entry']) / ev['entry'] * 100 if sig == 'BUY' \
                  else (ev['entry'] - stp) / ev['entry'] * 100
            return {'outcome': 'HIT_STOP', 'pnl': pnl, 'bars': j - i}
        if hit_target:
            pnl = (tgt - ev['entry']) / ev['entry'] * 100 if sig == 'BUY' \
                  else (ev['entry'] - tgt) / ev['entry'] * 100
            return {'outcome': 'HIT_TARGET', 'pnl': pnl, 'bars': j - i}

    exit_idx = min(i + MAX_BARS, n - 1)
    exit_px  = closes[exit_idx]
    pnl = (exit_px - ev['entry']) / ev['entry'] * 100 if sig == 'BUY' \
          else (ev['entry'] - exit_px) / ev['entry'] * 100
    return {'outcome': 'EXPIRED', 'pnl': pnl, 'bars': MAX_BARS}


# ── Summarise one condition's records ─────────────────────────────────────────

def summarise(records: list[dict]) -> dict:
    if not records:
        return {'signals': 0}
    closed  = [r for r in records if r['outcome'] != 'EXPIRED']
    wins    = [r for r in closed  if r['outcome'] == 'HIT_TARGET']
    losses  = [r for r in closed  if r['outcome'] == 'HIT_STOP']
    buys    = [r for r in records if r['signal']  == 'BUY']
    sells   = [r for r in records if r['signal']  == 'SELL']
    b_cl    = [r for r in buys  if r['outcome'] != 'EXPIRED']
    s_cl    = [r for r in sells if r['outcome'] != 'EXPIRED']
    b_wins  = [r for r in b_cl  if r['outcome'] == 'HIT_TARGET']
    s_wins  = [r for r in s_cl  if r['outcome'] == 'HIT_TARGET']

    win_pnl  = sum(r['pnl'] for r in wins)
    loss_pnl = abs(sum(r['pnl'] for r in losses))
    pf       = win_pnl / loss_pnl if loss_pnl else float('inf')
    avg_pnl  = sum(r['pnl'] for r in closed) / len(closed) if closed else 0

    return {
        'signals'     : len(records),
        'closed'      : len(closed),
        'wins'        : len(wins),
        'win_rate'    : len(wins)  / len(closed) * 100 if closed else 0,
        'avg_pnl'     : avg_pnl,
        'profit_factor': round(pf, 2),
        'buy_win_rate' : len(b_wins) / len(b_cl) * 100 if b_cl else 0,
        'sell_win_rate': len(s_wins) / len(s_cl) * 100 if s_cl else 0,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

CONDITIONS = ['NONE', 'SAME', 'NEARBY', 'IN_SQ']


# ── Backfill mode — write NEARBY historical signals to lag_signal_log ─────────

def backfill_db(candle_map: dict, gamma: float = 0.6) -> None:
    """
    Scan full history for NEARBY signals and upsert into lag_signal_log.
    Only BUY signals with POS momo and SELL signals with NEG momo are kept.
    Existing rows are not overwritten if already graded (outcome != OPEN).
    """
    from db import get_db
    db = get_db()

    # Fetch existing graded rows so we don't overwrite them
    existing = db.table('lag_signal_log').select(
        'ticker,signal_date,outcome'
    ).neq('outcome', 'OPEN').execute().data or []
    graded_keys = {(r['ticker'], r['signal_date']) for r in existing}

    total_rows = []
    for ticker, df in candle_map.items():
        try:
            events, fired, in_sq, pos_mo = all_signals(df, gamma)
            highs  = df['High'].values.astype(float)
            lows   = df['Low'].values.astype(float)
            closes = df['Close'].values.astype(float)
            n      = len(df)

            for ev in events:
                if not passes_condition(ev, 'NEARBY', fired, in_sq, pos_mo, n):
                    continue
                if (ticker, ev['date']) in graded_keys:
                    continue   # don't overwrite already-resolved rows

                g = grade(ev, highs, lows, closes, n)
                if g['outcome'] == 'EXPIRED':
                    continue   # skip signals with no resolution within MAX_BARS

                total_rows.append({
                    'ticker'       : ticker,
                    'signal_date'  : ev['date'],
                    'signal'       : ev['signal'],
                    'entry'        : round(ev['entry'], 2),
                    'target'       : round(ev['target'], 2),
                    'stop_price'   : round(ev['stop'], 2),
                    'outcome'      : g['outcome'],
                    'outcome_date' : None,   # we don't track the exact outcome date in backtest
                    'outcome_price': round(ev['target'] if g['outcome'] == 'HIT_TARGET'
                                         else ev['stop'], 2),
                    'pnl_pct'      : round(g['pnl'], 4),
                })
        except Exception as e:
            print(f"  {ticker}: {e}", flush=True)

    if not total_rows:
        print("No rows to backfill.")
        return

    # Upsert in chunks of 500
    CHUNK = 500
    written = 0
    for i in range(0, len(total_rows), CHUNK):
        chunk = total_rows[i:i+CHUNK]
        db.table('lag_signal_log').upsert(
            chunk, on_conflict='ticker,signal_date'
        ).execute()
        written += len(chunk)
        print(f"  {written}/{len(total_rows)} rows written…", flush=True)

    print(f"\nBackfill complete: {written} signals written to lag_signal_log")

    # Quick summary
    wins  = [r for r in total_rows if r['outcome'] == 'HIT_TARGET']
    total = len(total_rows)
    print(f"Win rate: {len(wins)}/{total} = {len(wins)/total*100:.1f}%  "
          f"Avg P&L: {sum(r['pnl_pct'] for r in total_rows)/total:+.2f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gamma',   type=float, nargs='+', default=GAMMAS)
    parser.add_argument('--cond',    type=str,   nargs='+', default=CONDITIONS,
                        choices=CONDITIONS)
    parser.add_argument('--tickers', type=str,   nargs='+', default=None)
    parser.add_argument('--min-bars',type=int,   default=150)
    parser.add_argument('--backfill',action='store_true',
                        help='Write NEARBY historical signals to lag_signal_log DB')
    args = parser.parse_args()

    db = get_db()
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        resp    = db.table('ticker_universe').select('ticker').execute()
        tickers = sorted(r['ticker'] for r in resp.data)

    print(f"Loading candles for {len(tickers)} tickers…", flush=True)
    from scanner import load_daily_candles
    candle_map: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers):
        df = load_daily_candles(t)
        if len(df) >= args.min_bars:
            candle_map[t] = df
        if (i + 1) % 150 == 0:
            print(f"  {i+1}/{len(tickers)}", flush=True)
    print(f"Loaded {len(candle_map)} tickers\n", flush=True)

    if args.backfill:
        print("Backfilling lag_signal_log with NEARBY signals (γ=0.6)…", flush=True)
        backfill_db(candle_map, gamma=0.6)
        return

    # results[gamma][condition] = summary dict
    results: dict[float, dict[str, dict]] = {}

    for gamma in args.gamma:
        results[gamma] = {c: [] for c in args.cond}
        t0 = time.time()

        for ticker, df in candle_map.items():
            try:
                events, fired, in_sq, pos_mo = all_signals(df, gamma)
                if not events:
                    continue
                highs  = df['High'].values.astype(float)
                lows   = df['Low'].values.astype(float)
                closes = df['Close'].values.astype(float)
                n      = len(df)

                for ev in events:
                    g = grade(ev, highs, lows, closes, n)
                    for cond in args.cond:
                        if passes_condition(ev, cond, fired, in_sq, pos_mo, n):
                            results[gamma][cond].append({**ev, **g})
            except Exception:
                pass

        elapsed = round(time.time() - t0, 1)
        sums = {c: summarise(results[gamma][c]) for c in args.cond}
        print(f"γ={gamma:.1f}  ({elapsed}s)")
        for cond in args.cond:
            s = sums[cond]
            print(
                f"  {cond:8s}  signals={s.get('signals',0):5d}"
                f"  win%={s.get('win_rate',0):5.1f}%"
                f"  avg_pnl={s.get('avg_pnl',0):+6.2f}%"
                f"  PF={s.get('profit_factor',0):5.2f}"
                f"  BUY%={s.get('buy_win_rate',0):5.1f}%"
                f"  SELL%={s.get('sell_win_rate',0):5.1f}%"
            )
        print()

    # ── Final summary table ───────────────────────────────────────────────────
    print("\n" + "="*100)
    print(f"{'GAMMA':>6}  {'CONDITION':>8}  {'SIGNALS':>8}  {'WIN%':>6}  "
          f"{'AVG P&L':>8}  {'P.FACTOR':>9}  {'BUY%':>6}  {'SELL%':>6}")
    print("-"*100)

    best_win = 0
    best_tag = ''
    for gamma in args.gamma:
        for cond in args.cond:
            s = summarise(results[gamma][cond])
            wr = s.get('win_rate', 0)
            if wr > best_win and s.get('signals', 0) >= 50:
                best_win = wr
                best_tag = f"{gamma:.1f}+{cond}"

    for gamma in args.gamma:
        for cond in args.cond:
            s = summarise(results[gamma][cond])
            tag = f"{gamma:.1f}+{cond}"
            marker = "  ◄ BEST" if tag == best_tag else ""
            print(
                f"  {gamma:.1f}    {cond:8s}  {s.get('signals',0):8d}"
                f"  {s.get('win_rate',0):6.1f}%  {s.get('avg_pnl',0):+8.2f}%"
                f"  {s.get('profit_factor',0):9.2f}"
                f"  {s.get('buy_win_rate',0):6.1f}%  {s.get('sell_win_rate',0):6.1f}%"
                f"{marker}"
            )
    print("="*100)


if __name__ == '__main__':
    main()
