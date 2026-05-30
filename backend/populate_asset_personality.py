#!/usr/bin/env python3
"""
populate_asset_personality.py
──────────────────────────────
Runs 365-day backtests for all active futures (10 symbols) across
all 24 hours and 3 VBH models (AGG, CON, WIDE) then upserts the
results into the asset_personality table.

Run AFTER applying migration 003_asset_personality.sql.

Usage:
    /opt/homebrew/bin/python3.11 populate_asset_personality.py
"""

import os, sys, time
from collections import defaultdict
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv('.env')

from db import get_db, get_30min
import pytz

ET = pytz.timezone('America/New_York')

# ── Contract specs: lot_value = $ per 1 price-unit move (1 contract) ──────────
FUTURES_CONFIG = {
    '/ES':  {'lot': 50,     'buf': 2.0,    'desc': 'E-mini S&P 500'},
    '/NQ':  {'lot': 20,     'buf': 5.0,    'desc': 'E-mini Nasdaq-100'},
    '/YM':  {'lot': 5,      'buf': 10.0,   'desc': 'E-mini Dow Jones'},
    '/RTY': {'lot': 50,     'buf': 1.0,    'desc': 'E-mini Russell 2000'},
    '/GC':  {'lot': 100,    'buf': 2.0,    'desc': 'Gold (100 oz)'},
    '/PL':  {'lot': 50,     'buf': 2.0,    'desc': 'Platinum (50 oz)'},
    '/SI':  {'lot': 5000,   'buf': 0.05,   'desc': 'Silver (5000 oz)'},
    '/CL':  {'lot': 1000,   'buf': 0.05,   'desc': 'WTI Crude Oil'},
    '/HG':  {'lot': 25000,  'buf': 0.005,  'desc': 'Copper (25000 lbs)'},
    '/BTC': {'lot': 5,      'buf': 500.0,  'desc': 'Bitcoin Futures'},
}
RTH_HOURS = set(range(9, 16))    # H9–H15
OFF_HOURS  = set(range(0, 9)) | set(range(16, 24))
MODELS     = ['AGG', 'CON', 'WIDE']
LOOKBACK   = 365


def classify_bias(long_net: float, short_net: float, total_net: float) -> str:
    if total_net <= 0:
        return 'AVOID'
    if long_net > 0 and short_net <= 0:
        return 'LONG'
    if short_net > 0 and long_net <= 0:
        return 'SHORT'
    if long_net > short_net * 2:
        return 'LONG'
    if short_net > long_net * 2:
        return 'SHORT'
    return 'NEUTRAL'


def classify_strength(win_rate: float, total_net: float, trades: int) -> str:
    if total_net <= 0 or trades < 10:
        return 'DEAD'
    if win_rate < 12:
        return 'DEAD'
    if win_rate >= 35:
        return 'STRONG'
    if win_rate >= 22:
        return 'MODERATE'
    return 'WEAK'


def run_full_backtest(bars: list, vbh_by_hour: dict, lot: float, buf: float) -> dict:
    """
    Returns a nested dict:  results[model][hour_et] = {stats}

    For each model × hour, signals are opened during that hour only,
    but carry across subsequent bars (incl. RTH) until target/stop.
    """
    sorted_bars = sorted(bars, key=lambda b: b['bar_time'])

    # Build hourly groups
    hourly: dict = defaultdict(list)
    for b in sorted_bars:
        bt = b['bar_time']
        if isinstance(bt, str):
            bt_dt = datetime.fromisoformat(bt.replace('Z', '+00:00'))
        else:
            bt_dt = bt
        et_dt = bt_dt.astimezone(ET)
        date_str = et_dt.strftime('%Y-%m-%d')
        hourly[(date_str, b['hour_et'])].append(b)

    results = {}
    for model in MODELS:
        results[model] = {}
        all_keys = sorted(hourly.keys())
        open_longs:  list = []
        open_shorts: list = []

        for key in all_keys:
            date_str, h = key
            h_bars = sorted(hourly[key], key=lambda b: b['bar_time'])
            if not h_bars:
                continue
            if h not in vbh_by_hour or model not in vbh_by_hour[h]:
                continue

            vbh  = vbh_by_hour[h][model]
            L1   = float(vbh['l1'])
            L3   = float(vbh['l3'])
            L4   = float(vbh['l4'])
            ref  = float(h_bars[0]['open'])
            if ref == 0:
                continue

            le = ref - L1;  se = ref + L1
            lt = ref + L4;  st = ref - L4
            ls = ref - L3 - buf;  ss = ref + L3 + buf
            ltrig = False;  strig = False

            for bar in h_bars:
                lo = float(bar['low'])
                hi = float(bar['high'])

                # Check exits on all open trades (any session)
                nl = []
                for t in open_longs:
                    if hi >= t['target']:
                        pnl = (t['target'] - t['entry']) * lot
                        _record(results, t['model'], t['hour'], 'L', 'WIN', pnl)
                    elif lo <= t['stop']:
                        pnl = (t['stop'] - t['entry']) * lot
                        _record(results, t['model'], t['hour'], 'L', 'LOSS', pnl)
                    else:
                        nl.append(t)
                open_longs = nl

                ns = []
                for t in open_shorts:
                    if lo <= t['target']:
                        pnl = (t['entry'] - t['target']) * lot
                        _record(results, t['model'], t['hour'], 'S', 'WIN', pnl)
                    elif hi >= t['stop']:
                        pnl = (t['entry'] - t['stop']) * lot
                        _record(results, t['model'], t['hour'], 'S', 'LOSS', pnl)
                    else:
                        ns.append(t)
                open_shorts = ns

                # Open new trades only in this model/hour signal
                if not ltrig and lo <= le:
                    ltrig = True
                    open_longs.append({'entry': le, 'target': lt, 'stop': ls,
                                       'model': model, 'hour': h, 'date': date_str, 'dir': 'L'})
                if not strig and hi >= se:
                    strig = True
                    open_shorts.append({'entry': se, 'target': st, 'stop': ss,
                                        'model': model, 'hour': h, 'date': date_str, 'dir': 'S'})

        # Close remaining open trades at last available close
        last_close = float(sorted_bars[-1]['close'])
        for t in open_longs:
            pnl = (last_close - t['entry']) * lot
            _record(results, t['model'], t['hour'], 'L', 'OPEN', pnl)
        for t in open_shorts:
            pnl = (t['entry'] - last_close) * lot
            _record(results, t['model'], t['hour'], 'S', 'OPEN', pnl)

    return results


def _record(results: dict, model: str, hour: int, direction: str, outcome: str, pnl: float):
    if hour not in results[model]:
        results[model][hour] = {
            'trades': [], 'long_trades': [], 'short_trades': []
        }
    entry = {'dir': direction, 'result': outcome, 'pnl': pnl}
    results[model][hour]['trades'].append(entry)
    if direction == 'L':
        results[model][hour]['long_trades'].append(entry)
    else:
        results[model][hour]['short_trades'].append(entry)


def summarise_hour(data: dict) -> dict:
    trades  = data.get('trades', [])
    longs   = data.get('long_trades', [])
    shorts  = data.get('short_trades', [])
    if not trades:
        return None

    wins   = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']
    wr     = len(wins) / len(trades) * 100

    lw = [t for t in longs  if t['result'] == 'WIN']
    sw = [t for t in shorts if t['result'] == 'WIN']

    lpnl = sum(t['pnl'] for t in longs)
    spnl = sum(t['pnl'] for t in shorts)
    tpnl = sum(t['pnl'] for t in trades)

    lwr = len(lw) / len(longs)  * 100 if longs  else 0
    swr = len(sw) / len(shorts) * 100 if shorts else 0

    return {
        'total_trades':   len(trades),
        'wins':           len(wins),
        'losses':         len(losses),
        'win_rate':       round(wr, 2),
        'net_pnl_usd':    round(tpnl, 2),
        'avg_pnl_usd':    round(tpnl / len(trades), 2),
        'long_trades':    len(longs),
        'long_wins':      len(lw),
        'long_win_rate':  round(lwr, 2),
        'long_net_usd':   round(lpnl, 2),
        'short_trades':   len(shorts),
        'short_wins':     len(sw),
        'short_win_rate': round(swr, 2),
        'short_net_usd':  round(spnl, 2),
        'direction_bias': classify_bias(lpnl, spnl, tpnl),
        'signal_strength': classify_strength(wr, tpnl, len(trades)),
    }


def main():
    db = get_db()

    # Load active futures in our focus list
    res = db.table('symbols').select('id,ticker').eq('is_active', True)\
            .eq('asset_type', 'future').execute()
    futures = [r for r in res.data if r['ticker'] in FUTURES_CONFIG]
    print(f"Futures to process: {[r['ticker'] for r in futures]}")

    all_rows = []
    for sym in futures:
        ticker    = sym['ticker']
        symbol_id = sym['id']
        cfg       = FUTURES_CONFIG[ticker]
        lot       = cfg['lot']
        buf       = cfg['buf']

        print(f"\n{'─'*60}")
        print(f"  {ticker}  ({cfg['desc']})  lot=${lot:,}/pt  buf={buf}")

        # Load VBH stats
        vbh_res = db.table('vbh_stats').select('*').eq('symbol_id', symbol_id).execute()
        vbh_by_hour: dict = {}
        for row in vbh_res.data:
            h = row['hour_et']
            if h not in vbh_by_hour:
                vbh_by_hour[h] = {}
            vbh_by_hour[h][row['model']] = row

        if not vbh_by_hour:
            print(f"  ⚠  No VBH stats found — skipping")
            continue

        # Load bars
        bars = get_30min(symbol_id, lookback_days=LOOKBACK)
        if len(bars) < 100:
            print(f"  ⚠  Only {len(bars)} bars — skipping")
            continue
        print(f"  Loaded {len(bars):,} bars from DB")

        # Run backtest
        t0 = time.time()
        results = run_full_backtest(bars, vbh_by_hour, lot, buf)
        print(f"  Backtest done in {time.time()-t0:.1f}s")

        # Build upsert rows
        for model in MODELS:
            hours_data = results.get(model, {})
            for h in range(24):
                data = hours_data.get(h)
                if not data:
                    continue
                summary = summarise_hour(data)
                if summary is None:
                    continue
                session = 'RTH' if h in RTH_HOURS else 'OFF'
                row = {
                    'symbol_id':      symbol_id,
                    'model':          model,
                    'hour_et':        h,
                    'session':        session,
                    'lot_value_usd':  lot,
                    'buf_pts':        buf,
                    'lookback_days':  LOOKBACK,
                    **summary,
                }
                all_rows.append(row)
                print(f"  {model} H{h:02d} ({session}): "
                      f"{summary['total_trades']:3d} trades  "
                      f"WR {summary['win_rate']:5.1f}%  "
                      f"P&L ${summary['net_pnl_usd']:>10,.0f}  "
                      f"[{summary['direction_bias']:7s}  {summary['signal_strength']}]")

    # Upsert all rows in batches of 50
    print(f"\n{'='*60}")
    print(f"Upserting {len(all_rows)} rows into asset_personality …")
    BATCH = 50
    for i in range(0, len(all_rows), BATCH):
        batch = all_rows[i:i+BATCH]
        db.table('asset_personality')\
          .upsert(batch, on_conflict='symbol_id,model,hour_et')\
          .execute()
    print(f"✓ Done — {len(all_rows)} rows written to asset_personality")


if __name__ == '__main__':
    main()
