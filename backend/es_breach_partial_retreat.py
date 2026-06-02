#!/usr/bin/env python3
"""
es_breach_partial_retreat.py
─────────────────────────────
After a valid CR breach (close ≥ 2 ticks beyond boundary), categorize
what happens next:
  1. No retreat   — price never came back into CR area
  2. Partial      — price re-entered CR zone but did NOT reach middle
  3. Full retreat — price reached CR middle (our existing backtest)

For partial retreats: how far did it dip, and did it close above CR top?
"""

import os
from collections import defaultdict
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv('.env')

from db import get_db, get_30min
import pytz

ET     = pytz.timezone('America/New_York')
TICK   = 0.25
BREACH = 2 * TICK   # 0.50 pts

def rth_sessions(bars):
    sessions = defaultdict(list)
    for b in bars:
        bt = b['bar_time']
        bt_dt = datetime.fromisoformat(bt.replace('Z','+00:00')) if isinstance(bt,str) else bt
        et    = bt_dt.astimezone(ET)
        h, m  = et.hour, et.minute
        if (h == 9 and m == 30) or (10 <= h <= 15):
            sessions[et.strftime('%Y-%m-%d')].append((et, b))
    return [(d, [x[1] for x in sorted(v)]) for d,v in sorted(sessions.items())]

def analyze():
    db        = get_db()
    sym       = db.table('symbols').select('id').eq('ticker','/ES').execute().data[0]['id']
    bars      = get_30min(sym, lookback_days=365)
    sessions  = rth_sessions(bars)
    print(f"Bars: {len(bars):,}   RTH sessions: {len(sessions)}")

    results = {'TOP': {'none':[], 'partial':[], 'full':[]},
               'BOT': {'none':[], 'partial':[], 'full':[]}}

    for date, day in sessions:
        if len(day) < 2:
            continue
        fb        = day[0]
        cr_top    = float(fb['high'])
        cr_bot    = float(fb['low'])
        cr_mid    = (cr_top + cr_bot) / 2
        cr_range  = cr_top - cr_bot
        if cr_range < 2:
            continue

        for direction, breach_test, side_label in [
            ('TOP', lambda c: c >= cr_top + BREACH, 'TOP'),
            ('BOT', lambda c: c <= cr_bot - BREACH, 'BOT'),
        ]:
            breach_idx = None
            for i, bar in enumerate(day[1:], 1):
                if breach_test(float(bar['close'])):
                    breach_idx = i
                    break
            if breach_idx is None:
                continue

            post = day[breach_idx+1:]
            if not post:
                results[direction]['none'].append({'date':date,'cr_range':cr_range})
                continue

            if direction == 'TOP':
                extremes   = [float(b['low']) for b in post]
                worst      = min(extremes)
                dip        = cr_top - worst          # positive = how far below cr_top
                in_cr      = worst < cr_top
                hit_mid    = worst <= cr_mid
                final_close= float(day[-1]['close'])
                outcome    = final_close >= cr_top   # closed back above CR top

                rec = {
                    'date': date, 'cr_top': cr_top, 'cr_bot': cr_bot,
                    'cr_mid': cr_mid, 'cr_range': round(cr_range,2),
                    'worst_low': worst,
                    'dip': round(dip, 2),
                    'dip_pct': round(dip / cr_range * 100, 1),
                    'final_close': final_close,
                    'success': outcome,   # closed above CR top = held the breakout
                }
            else:
                extremes   = [float(b['high']) for b in post]
                best       = max(extremes)
                bounce     = best - cr_bot
                in_cr      = best > cr_bot
                hit_mid    = best >= cr_mid
                final_close= float(day[-1]['close'])
                outcome    = final_close <= cr_bot   # closed back below CR bottom

                rec = {
                    'date': date, 'cr_top': cr_top, 'cr_bot': cr_bot,
                    'cr_mid': cr_mid, 'cr_range': round(cr_range,2),
                    'best_high': best,
                    'bounce': round(bounce, 2),
                    'bounce_pct': round(bounce / cr_range * 100, 1),
                    'final_close': final_close,
                    'success': outcome,
                }

            cat = 'full' if hit_mid else ('partial' if in_cr else 'none')
            results[direction][cat].append(rec)

    # ── Print ────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  /ES  Breach Follow-Through Analysis  |  365 days")
    print(f"{'='*65}")

    for direction in ('TOP','BOT'):
        none    = results[direction]['none']
        partial = results[direction]['partial']
        full    = results[direction]['full']
        total   = len(none)+len(partial)+len(full)
        if total == 0:
            continue

        label = "TOP BREACH (closed ≥ 0.50 above CR top)" if direction=='TOP' \
           else "BOT BREACH (closed ≥ 0.50 below CR bot)"
        print(f"\n── {label}  n={total} ─────────────────────")
        print(f"  Stayed outside CR (no retreat)  : {len(none):3d}  ({len(none)/total*100:.1f}%)")
        print(f"  Partial retreat (short of mid)  : {len(partial):3d}  ({len(partial)/total*100:.1f}%)")
        print(f"  Full retreat (reached middle)   : {len(full):3d}  ({len(full)/total*100:.1f}%)")

        if not partial:
            continue

        key   = 'dip'    if direction=='TOP' else 'bounce'
        pkey  = 'dip_pct' if direction=='TOP' else 'bounce_pct'
        vals  = [r[key]  for r in partial]
        pcts  = [r[pkey] for r in partial]
        wins  = sum(1 for r in partial if r['success'])
        lbl   = "closed above CR top" if direction=='TOP' else "closed below CR bot"

        print(f"\n  ── Partial retreat deep-dive (n={len(partial)}) ──────────────────")
        print(f"    Avg retreat depth  : {sum(vals)/len(vals):.1f} pts")
        print(f"    Median             : {sorted(vals)[len(vals)//2]:.1f} pts")
        print(f"    Min / Max          : {min(vals):.1f} / {max(vals):.1f} pts")
        print(f"    Avg % of CR range  : {sum(pcts)/len(pcts):.1f}%")
        print(f"    {lbl:30s}: {wins}/{len(partial)} ({wins/len(partial)*100:.1f}%)")

        print(f"\n  ── Dip distribution vs. success ──────────────────────────────")
        print(f"    {'Depth bucket':20s} {'Cases':>6}  {'Success':>8}  {'Rate':>6}")
        buckets = [(0,2,'0–2'), (2,5,'2–5'), (5,10,'5–10'),
                   (10,15,'10–15'), (15,25,'15–25'), (25,999,'25+')]
        for lo, hi, lbl2 in buckets:
            subset = [r for r in partial if lo < r[key] <= hi]
            if not subset: continue
            w = sum(1 for r in subset if r['success'])
            print(f"    {lbl2+' pts':20s} {len(subset):6d}  {w:>8d}  {w/len(subset)*100:>5.0f}%")

