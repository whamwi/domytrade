"""
Market Profile Rule Engine — Backtest Runner
Futures only: /ES, /NQ, /RTY

Usage:
    python backtest_mp_rules.py --symbols /NQ /ES --days 90
    python backtest_mp_rules.py --symbols /NQ --days 60 --csv out.csv

Evaluates:
    R08  Day Type Estimate accuracy (estimated at 10:30 vs actual end-of-session)
    R10  First Extension trade performance (entry / stop / unrealized target)
    R11  How often extension invalidates and at what cost
"""

import sys
import os
import csv
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ─── Path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schwab_client import get_candles   # uses token from token.json
import market_profile_rules as MP

try:
    import pytz
    ET = pytz.timezone('America/New_York')
except ImportError:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo('America/New_York')

# ─── Tick sizes per symbol ─────────────────────────────────────────────────────
TICKS = {
    '/ES':  0.25,
    '/NQ':  0.25,
    '/RTY': 0.10,
    '/YM':  1.0,
    '/GC':  0.10,
    '/CL':  0.01,
    '/ZB':  0.03125,
    '/SI':  0.005,
}

DEFAULT_TICK = 0.25

RTH_START_MIN = 9 * 60 + 30    # 9:30 AM ET
RTH_END_MIN   = 16 * 60         # 4:00 PM ET


# ─────────────────────────────────────────────────────────────────────────────
# Profile builders (standalone copies — no FastAPI dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _bar_et(b: dict) -> datetime:
    return datetime.fromtimestamp(b['datetime'] / 1000, tz=timezone.utc).astimezone(ET)


def build_rth_profile(bars: list[dict], tick: float) -> dict:
    """Build full RTH TPO letter profile from 1-min bars.
    A=9:30, B=10:00, … M=15:30 (13 × 30-min periods).
    """
    price_letters: dict = defaultdict(set)
    period_ranges: dict = {}
    period_last_dt: dict = {}
    open_price = None
    open_dt = None

    for b in bars:
        dt    = _bar_et(b)
        t_min = dt.hour * 60 + dt.minute
        if not (RTH_START_MIN <= t_min < RTH_END_MIN):
            continue
        period_idx = (t_min - RTH_START_MIN) // 30
        if not (0 <= period_idx <= 12):
            continue
        letter = chr(ord('A') + period_idx)

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

    tpo_map = {p: len(ls) for p, ls in price_letters.items()}
    total   = sum(tpo_map.values())
    poc     = max(tpo_map, key=tpo_map.get)
    prices  = sorted(tpo_map.keys())
    poc_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - poc))

    va_set  = {prices[poc_idx]}
    va_tpos = tpo_map[prices[poc_idx]]
    lo_idx  = poc_idx
    hi_idx  = poc_idx
    target  = total * 0.70
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


def build_overnight_profile(bars: list[dict], tick: float) -> dict:
    """Build overnight TPO profile (6:00 PM – 9:30 AM ET)."""
    ON_LETTERS = list('abcdefghijklmnopqrstuvwxyz') + ['1', '2', '3', '4', '5']
    price_letters: dict = defaultdict(set)
    period_ranges: dict = {}

    for b in bars:
        dt    = _bar_et(b)
        t_min = dt.hour * 60 + dt.minute
        if t_min >= 18 * 60:
            period_idx = (t_min - 18 * 60) // 30
        elif t_min < RTH_START_MIN:
            period_idx = 12 + t_min // 30
        else:
            continue   # RTH bar
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

    _empty = {'profile': [], 'poc': None, 'vah': None, 'val': None,
              'single_prints': [], 'periods': 0, 'period_ranges': {},
              'session_high': None, 'session_low': None}
    if not price_letters:
        return _empty

    tpo_map = {p: len(ls) for p, ls in price_letters.items()}
    total   = sum(tpo_map.values())
    poc     = max(tpo_map, key=tpo_map.get)
    prices  = sorted(tpo_map.keys())
    poc_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - poc))

    va_set  = {prices[poc_idx]}
    va_tpos = tpo_map[prices[poc_idx]]
    lo_idx  = poc_idx
    hi_idx  = poc_idx
    target  = total * 0.70
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
    high_r = max(r['price'] for r in profile) if profile else None
    low_r  = min(r['price'] for r in profile) if profile else None

    return {
        'profile':       profile,
        'poc':           round(poc, 2),
        'vah':           round(max(va_set), 2),
        'val':           round(min(va_set), 2),
        'single_prints': sorted([round(p, 2) for p, c in tpo_map.items() if c == 1], reverse=True),
        'periods':       len(period_ranges),
        'period_ranges': period_ranges,
        'session_high':  high_r,
        'session_low':   low_r,
        # expose high/low as named keys too (matches overnight dict contract)
        'high':          high_r,
        'low':           low_r,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data slicing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rth_date_key(b: dict) -> str:
    """YYYY-MM-DD ET date of bar's RTH session."""
    dt    = _bar_et(b)
    t_min = dt.hour * 60 + dt.minute
    # Bars before 9:30 belong to the coming RTH session
    if t_min < RTH_START_MIN:
        return (dt.date() + timedelta(days=-1 if dt.weekday() == 0 else 0)).isoformat()
    return dt.date().isoformat()


def slice_sessions(all_bars: list[dict]) -> dict[str, dict]:
    """Slice all bars into per-date dicts of {rth_bars, on_bars}."""
    rth: dict[str, list] = defaultdict(list)
    pre: dict[str, list] = defaultdict(list)   # overnight bars keyed by next RTH date

    for b in all_bars:
        dt    = _bar_et(b)
        t_min = dt.hour * 60 + dt.minute
        date_str = dt.date().isoformat()

        if RTH_START_MIN <= t_min < RTH_END_MIN:
            rth[date_str].append(b)
        elif t_min >= 18 * 60:
            # Evening of day X → overnight for next RTH session
            next_day = (dt.date() + timedelta(days=1)).isoformat()
            pre[next_day].append(b)
        elif t_min < RTH_START_MIN:
            # Pre-market → overnight for today's RTH
            pre[date_str].append(b)

    all_dates = sorted(set(rth.keys()) | set(pre.keys()))
    return {
        d: {
            'rth_bars': rth.get(d, []),
            'on_bars':  pre.get(d, []),
        }
        for d in all_dates
        if rth.get(d)   # only dates that have RTH data
    }


# ─────────────────────────────────────────────────────────────────────────────
# Actual day type classifier  (end-of-session ground truth)
# ─────────────────────────────────────────────────────────────────────────────

def classify_actual_day_type(prof: dict) -> str:
    ib_high = prof.get('ib_high')
    ib_low  = prof.get('ib_low')
    sh      = prof.get('session_high')
    sl      = prof.get('session_low')

    if ib_high is None or ib_low is None or sh is None or sl is None:
        return 'UNKNOWN'

    ib_range   = ib_high - ib_low
    if ib_range <= 0:
        return 'UNKNOWN'

    high_ext = max(0.0, sh - ib_high)
    low_ext  = max(0.0, ib_low - sl)

    both_sides  = high_ext > ib_range * 0.30 and low_ext > ib_range * 0.30
    trend_bull  = high_ext > ib_range * 1.50 and low_ext < ib_range * 0.20
    trend_bear  = low_ext  > ib_range * 1.50 and high_ext < ib_range * 0.20
    norm_var    = (high_ext > ib_range * 0.50 or low_ext > ib_range * 0.50) and not both_sides
    no_ext      = high_ext < ib_range * 0.10 and low_ext  < ib_range * 0.10
    full_sess   = sh - sl  # total session range
    wide_ib     = ib_range >= (full_sess * 0.70)  # IB held most of session

    if both_sides:
        return 'NEUTRAL'
    if trend_bull:
        return 'TREND_BULL'
    if trend_bear:
        return 'TREND_BEAR'
    if norm_var:
        return 'NORMAL_VARIATION'
    if wide_ib or no_ext:
        return 'NORMAL'
    return 'NON_TREND'


# ─────────────────────────────────────────────────────────────────────────────
# R10 trade simulation
# ─────────────────────────────────────────────────────────────────────────────

def simulate_r10_trade(session_prof: dict, tick: float) -> dict | None:
    """
    Simulate R10 first extension trade.
    Entry: first period close outside IB.
    Stop:  first period close back inside IB (Dalton's invalidation rule).
    Target: session high/low at end of session (unrealized max favourable).
    Returns None if no extension fires.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    pr      = session_prof.get('period_ranges', {})

    if ib_high is None or ib_low is None:
        return None

    post_ib = [(ltr, d) for ltr, d in pr.items()
               if ltr >= 'C' and d.get('close') is not None]

    if not post_ib:
        return None

    # Find first extension
    direction = None
    entry_ltr = None
    entry_px  = None
    for ltr, d in post_ib:
        c = d['close']
        if c > ib_high + tick:
            direction = 'LONG'
            entry_ltr = ltr
            entry_px  = c
            break
        if c < ib_low - tick:
            direction = 'SHORT'
            entry_ltr = ltr
            entry_px  = c
            break

    if direction is None:
        return None

    stop_px    = None
    exit_ltr   = None
    exit_px    = None
    outcome    = 'OPEN'
    max_fav    = 0.0
    max_adv    = 0.0
    entry_idx  = [ltr for ltr, _ in post_ib].index(entry_ltr)

    for ltr, d in post_ib[entry_idx + 1:]:
        c = d['close']
        if direction == 'LONG':
            fav = c - entry_px
            if fav > max_fav: max_fav = fav
            adv = entry_px - c
            if adv > max_adv: max_adv = adv
            if c < ib_low - tick:
                stop_px  = c
                exit_ltr = ltr
                exit_px  = c
                outcome  = 'INVALIDATED'
                break
            elif c >= ib_low and c <= ib_high + tick:
                stop_px  = c
                exit_ltr = ltr
                exit_px  = c
                outcome  = 'STOPPED_AT_IB'
                break
        else:   # SHORT
            fav = entry_px - c
            if fav > max_fav: max_fav = fav
            adv = c - entry_px
            if adv > max_adv: max_adv = adv
            if c > ib_high + tick:
                stop_px  = c
                exit_ltr = ltr
                exit_px  = c
                outcome  = 'INVALIDATED'
                break
            elif c >= ib_low - tick and c <= ib_high:
                stop_px  = c
                exit_ltr = ltr
                exit_px  = c
                outcome  = 'STOPPED_AT_IB'
                break

    if outcome == 'OPEN':
        # Position rode to session close
        sh = session_prof.get('session_high')
        sl = session_prof.get('session_low')
        if direction == 'LONG':
            exit_px = sh   # best possible
            outcome = 'SESSION_CLOSE'
        else:
            exit_px = sl
            outcome = 'SESSION_CLOSE'
        exit_ltr = 'M'   # last period

    pnl = (exit_px - entry_px) if direction == 'LONG' else (entry_px - exit_px)
    pnl_ticks = pnl / tick if tick else pnl

    return {
        'direction':   direction,
        'entry_ltr':   entry_ltr,
        'entry_px':    entry_px,
        'exit_ltr':    exit_ltr,
        'exit_px':     exit_px,
        'outcome':     outcome,
        'pnl_pts':     round(pnl, 4),
        'pnl_ticks':   round(pnl_ticks, 1),
        'max_fav_pts': round(max_fav, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main backtest loop
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(symbol: str, lookback_days: int,
                 verbose: bool = False) -> list[dict]:
    tick = TICKS.get(symbol, DEFAULT_TICK)

    print(f'\n[{symbol}] Fetching {lookback_days} days of 1-min bars …')
    bars = get_candles(symbol, lookback_days=lookback_days, freq_min=1)
    if not bars:
        print(f'  No bars returned — is the Schwab token valid?')
        return []

    print(f'  {len(bars):,} bars fetched.')
    sessions = slice_sessions(bars)
    sorted_dates = sorted(sessions.keys())

    results = []
    prior_prof = None

    for i, date_str in enumerate(sorted_dates):
        sess     = sessions[date_str]
        rth_bars = sess['rth_bars']
        on_bars  = sess['on_bars']

        if len(rth_bars) < 60:   # need at least 60 min of data (A+B complete)
            continue

        prof = build_rth_profile(rth_bars, tick)
        on   = build_overnight_profile(on_bars, tick)

        if prof.get('ib_high') is None or prof.get('ib_low') is None:
            continue

        prior_rth = prior_prof or {}
        dt_930 = datetime.fromisoformat(date_str).replace(
            hour=10, minute=30, tzinfo=ET)   # Phase 2 evaluation time

        p2 = MP.run_phase2(prof, on, prior_rth, tick, dt_930)
        p3 = MP.run_phase3(prof, on, tick)

        r08 = next((r for r in p2.results if r.rule_id == 'R08'), None)
        r10 = next((r for r in p3.results if r.rule_id == 'R10'), None)

        actual = classify_actual_day_type(prof)
        trade  = simulate_r10_trade(prof, tick)

        r08_state  = r08.state if r08 and r08.fired else 'NO_DATA'
        r10_state  = r10.state if r10 and r10.fired else 'NO_DATA'
        r10_acc    = _r10_match(r08_state, actual)

        row = {
            'date':          date_str,
            'symbol':        symbol,
            'ib_range':      prof.get('ib_range'),
            'session_high':  prof.get('session_high'),
            'session_low':   prof.get('session_low'),
            'actual_day':    actual,
            'r08_estimate':  r08_state,
            'r08_match':     r10_acc,
            'r10_state':     r10_state,
            'trade_dir':     trade['direction']  if trade else None,
            'trade_outcome': trade['outcome']    if trade else None,
            'trade_pnl_pts': trade['pnl_pts']    if trade else None,
            'trade_pnl_ticks': trade['pnl_ticks'] if trade else None,
            'max_fav_pts':   trade['max_fav_pts'] if trade else None,
        }
        results.append(row)
        prior_prof = {**prof, 'session_high': prof['session_high'], 'session_low': prof['session_low']}

        if verbose:
            print(f'  {date_str} | actual={actual:<20} | r08={r08_state:<28} | match={r10_acc} '
                  f'| trade={trade["outcome"] if trade else "NONE":<18} '
                  f'| pnl={trade["pnl_pts"] if trade else 0:+.2f}')

    return results


def _r10_match(r08_state: str, actual: str) -> str:
    """Rough match between R08 estimate and actual day type."""
    exact = {
        'TREND_DAY':           ('TREND_BULL', 'TREND_BEAR'),
        'NORMAL_VARIATION_DAY':('NORMAL_VARIATION',),
        'NEUTRAL_DAY':         ('NEUTRAL',),
        'NORMAL_DAY':          ('NORMAL',),
        'FAILED_TREND_ATTEMPT':('NORMAL_VARIATION', 'NORMAL', 'NON_TREND'),
        'NON_TREND_DAY':       ('NON_TREND',),
    }
    for est_key, actuals in exact.items():
        if est_key in r08_state and actual in actuals:
            return 'HIT'
    return 'MISS'


# ─────────────────────────────────────────────────────────────────────────────
# Summary statistics
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: list[dict], symbol: str) -> None:
    if not results:
        print('No results.')
        return

    total = len(results)
    print(f'\n{"═"*60}')
    print(f'  BACKTEST SUMMARY — {symbol}  ({total} sessions)')
    print(f'{"═"*60}')

    # ── R08 accuracy ─────────────────────────────────────────────────────────
    hits  = sum(1 for r in results if r['r08_match'] == 'HIT')
    acc   = hits / total * 100
    print(f'\n  R08 Day Type Estimate Accuracy: {hits}/{total} = {acc:.1f}%')

    actual_counts = defaultdict(int)
    for r in results:
        actual_counts[r['actual_day']] += 1
    print('  Actual day type distribution:')
    for dt, cnt in sorted(actual_counts.items(), key=lambda x: -x[1]):
        print(f'    {dt:<25} {cnt:3d}  ({cnt/total*100:.0f}%)')

    # ── R10 trade performance ─────────────────────────────────────────────────
    trades  = [r for r in results if r['trade_dir'] is not None]
    no_ext  = total - len(trades)
    print(f'\n  R10 First Extension fired: {len(trades)}/{total} sessions ({len(trades)/total*100:.0f}%)')
    print(f'  No extension (price stayed inside IB): {no_ext} ({no_ext/total*100:.0f}%)')

    if trades:
        wins   = [t for t in trades if (t['trade_pnl_pts'] or 0) > 0]
        losses = [t for t in trades if (t['trade_pnl_pts'] or 0) < 0]
        wr     = len(wins) / len(trades) * 100
        avg_w  = sum(t['trade_pnl_pts'] for t in wins)  / max(len(wins),  1)
        avg_l  = sum(t['trade_pnl_pts'] for t in losses)/ max(len(losses), 1)
        total_pnl = sum(t['trade_pnl_pts'] or 0 for t in trades)
        pf    = abs(sum(t['trade_pnl_pts'] for t in wins)) / max(abs(sum(t['trade_pnl_pts'] for t in losses)), 0.01)

        print(f'\n  Trade Statistics (R10 entry → invalidation rule exit):')
        print(f'    Win rate:        {wr:.1f}%  ({len(wins)}W / {len(losses)}L)')
        print(f'    Avg win:         {avg_w:+.2f} pts')
        print(f'    Avg loss:        {avg_l:+.2f} pts')
        print(f'    Profit factor:   {pf:.2f}')
        print(f'    Total PnL:       {total_pnl:+.2f} pts')

        outcomes = defaultdict(int)
        for t in trades:
            outcomes[t['trade_outcome']] += 1
        print('\n  Trade outcome breakdown:')
        for oc, cnt in sorted(outcomes.items(), key=lambda x: -x[1]):
            print(f'    {oc:<20} {cnt:3d}')

        long_t  = [t for t in trades if t['trade_dir'] == 'LONG']
        short_t = [t for t in trades if t['trade_dir'] == 'SHORT']
        if long_t:
            l_wr = sum(1 for t in long_t if (t['trade_pnl_pts'] or 0) > 0) / len(long_t) * 100
            print(f'\n  Long trades:  {len(long_t)} | WR={l_wr:.0f}%')
        if short_t:
            s_wr = sum(1 for t in short_t if (t['trade_pnl_pts'] or 0) > 0) / len(short_t) * 100
            print(f'  Short trades: {len(short_t)} | WR={s_wr:.0f}%')

    print(f'\n{"═"*60}\n')


def write_csv(results: list[dict], path: str) -> None:
    if not results:
        return
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f'CSV written to {path}')


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Market Profile Rule Engine Backtest')
    parser.add_argument('--symbols', nargs='+', default=['/NQ', '/ES'],
                        help='Futures symbols (default: /NQ /ES)')
    parser.add_argument('--days', type=int, default=60,
                        help='Lookback days (default: 60, ~42 trading days)')
    parser.add_argument('--csv', type=str, default=None,
                        help='Optional CSV output path')
    parser.add_argument('--verbose', action='store_true',
                        help='Print each session line')
    args = parser.parse_args()

    all_results = []
    for sym in args.symbols:
        results = run_backtest(sym, args.days, verbose=args.verbose)
        print_summary(results, sym)
        all_results.extend(results)

    if args.csv and all_results:
        write_csv(all_results, args.csv)


if __name__ == '__main__':
    main()
