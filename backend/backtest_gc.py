"""
Backtest: OA Open + B Accepted — GC (Gold Futures)
Same signal logic as ES: LONG only (backtest proven regime)

Session: 9:30 AM – 4:00 PM ET (same as app)
Overnight: 6 PM prev day → 9:30 AM ET
Tick: $0.10

Entry C: 1+1 blend — 1 at C open + 1 on pullback to ONH
Stop: ON POC (overnight VWAP proxy)
Exit: Stop hit or M close
"""
from dotenv import load_dotenv
load_dotenv('/Users/wassim/domytrade/backend/.env')

from db import get_db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import statistics

ET      = ZoneInfo('America/New_York')
TICK    = 0.10
SYM_ID  = 11   # /GC

# ── Fetch GC 30-min bars ──────────────────────────────────────────────────────
print("Fetching GC 30-min bars…")
bars, start = [], 0
while True:
    res = (get_db().table('ohlc_30min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', SYM_ID).order('bar_time')
           .range(start, start + 999).execute())
    bars.extend(res.data)
    if len(res.data) < 1000: break
    start += 1000

for b in bars:
    b['dt']   = datetime.fromisoformat(b['bar_time']).astimezone(ET)
    b['date'] = b['dt'].strftime('%Y-%m-%d')
    b['hm']   = (b['dt'].hour, b['dt'].minute)

by_date = defaultdict(list)
for b in bars:
    by_date[b['date']].append(b)

print(f"  {len(bars)} bars  |  {bars[0]['date']} → {bars[-1]['date']}\n")


def overnight_levels(rth_date_str):
    rth_dt  = datetime.strptime(rth_date_str, '%Y-%m-%d')
    on_bars = []
    for days_back in [3, 2, 1]:
        pd = (rth_dt - timedelta(days=days_back)).strftime('%Y-%m-%d')
        for b in by_date.get(pd, []):
            if b['hm'][0] >= 18:
                on_bars.append(b)
    for b in by_date.get(rth_date_str, []):
        if b['hm'] < (9, 30):
            on_bars.append(b)
    if len(on_bars) < 4:
        return None
    on_high   = max(b['high'] for b in on_bars)
    on_low    = min(b['low']  for b in on_bars)
    total_vol = sum(b['volume'] or 1 for b in on_bars)
    vwap      = sum(((b['high']+b['low']+b['close'])/3)*(b['volume'] or 1)
                    for b in on_bars) / total_vol
    def rt(x): return round(round(x / TICK) * TICK, 2)
    return {'high': rt(on_high), 'low': rt(on_low), 'poc': rt(vwap)}


RTH_LETTERS = {
    (9,30):'A', (10,0):'B', (10,30):'C', (11,0):'D', (11,30):'E',
    (12,0):'F', (12,30):'G', (13,0):'H', (13,30):'I', (14,0):'J',
    (14,30):'K', (15,0):'L', (15,30):'M',
}

def rth_periods(date_str):
    p = {}
    for b in by_date.get(date_str, []):
        ltr = RTH_LETTERS.get(b['hm'])
        if ltr:
            p[ltr] = b
    return p


def simulate_blend(direction, c_open, pullback_lvl, stop_lvl, pr, c_hm):
    post = [l for hm, l in sorted(RTH_LETTERS.items()) if hm > c_hm]
    c1_entry = c_open
    c2_entry = None
    exit_px  = None
    stopped  = False

    for ltr in post:
        bar = pr.get(ltr)
        if bar is None: continue
        if direction == 'LONG':
            if bar['low'] <= stop_lvl:
                stopped = True; exit_px = stop_lvl; break
            if c2_entry is None and bar['low'] <= pullback_lvl + TICK:
                c2_entry = pullback_lvl
        else:
            if bar['high'] >= stop_lvl:
                stopped = True; exit_px = stop_lvl; break
            if c2_entry is None and bar['high'] >= pullback_lvl - TICK:
                c2_entry = pullback_lvl

    if not stopped:
        last = pr.get('M') or pr.get('L') or pr.get('K')
        if last: exit_px = last['close']

    if exit_px is None: return None

    pnl_c1 = (exit_px - c1_entry) if direction == 'LONG' else (c1_entry - exit_px)
    pnl_c2 = ((exit_px - c2_entry) if direction == 'LONG' else (c2_entry - exit_px)) if c2_entry else None
    total  = pnl_c1 + (pnl_c2 or 0)
    risk   = abs(c1_entry - stop_lvl) + (abs(c2_entry - stop_lvl) if c2_entry else 0)

    return {
        'stopped':   stopped, 'exit_px': exit_px,
        'c1_entry':  c1_entry, 'c1_pnl': round(pnl_c1, 2),
        'c2_filled': c2_entry is not None, 'c2_entry': c2_entry,
        'c2_pnl':    round(pnl_c2, 2) if pnl_c2 is not None else None,
        'total_pnl': round(total, 2),
        'total_risk':round(risk, 2),
        'contracts': 2 if c2_entry else 1,
    }


# ── Main loop — LONG only ─────────────────────────────────────────────────────
trades = []
all_dates = sorted(set(b['date'] for b in bars))

for date in all_dates:
    on = overnight_levels(date)
    pr = rth_periods(date)
    if not on or not all(k in pr for k in ('A','B','C')): continue

    on_high, on_low, on_poc = on['high'], on['low'], on['poc']
    open_px = pr['A']['open']

    # OA open: open inside overnight range
    if not (on_low - TICK <= open_px <= on_high + TICK): continue

    # B accepted above ONH (LONG signal only)
    b_close = pr['B']['close']
    if not (b_close > on_high + TICK): continue

    stop_lvl     = on_poc
    pullback_lvl = on_high
    c_open       = pr['C']['open']
    c_hm         = pr['C']['hm']

    if stop_lvl >= pullback_lvl: continue
    if c_open <= stop_lvl: continue

    t = simulate_blend('LONG', c_open, pullback_lvl, stop_lvl, pr, c_hm)
    if t:
        trades.append({'date': date, 'on_high': on_high, 'on_poc': on_poc, **t})


# ── Results ───────────────────────────────────────────────────────────────────
n = len(trades)
print(f"{'═'*64}")
print(f"  GC — OA Open + B Accepted Above ONH — LONG only")
print(f"  1+1 Blend: C-open + pullback to ONH | Stop: ON POC")
print(f"{'═'*64}")
print(f"  Signal days   : {n}")
if not n: print("  No trades found."); exit()

total_pnls = [t['total_pnl'] for t in trades]
c1_pnls    = [t['c1_pnl']    for t in trades]
c2_filled  = [t for t in trades if t['c2_filled']]
c2_pnls    = [t['c2_pnl'] for t in c2_filled]
stops      = [t for t in trades if t['stopped']]
wins       = [t for t in trades if t['total_pnl'] > 0]

gw = sum(p for p in total_pnls if p > 0)
gl = sum(p for p in total_pnls if p < 0)
pf = gw / abs(gl) if gl else float('inf')

print(f"  — 2 contracts  : {len(c2_filled)} days ({100*len(c2_filled)//n}%) — pullback filled")
print(f"  — 1 contract   : {n-len(c2_filled)} days ({100*(n-len(c2_filled))//n}%) — market ran")
print()
print(f"  Win rate       : {len(wins)}/{n}  ({100*len(wins)//n}%)")
print(f"  Stop-out rate  : {len(stops)}/{n}  ({100*len(stops)//n}%)")
print(f"  Total P&L      : {sum(total_pnls):+.2f} pts")
print(f"  Avg/signal     : {sum(total_pnls)/n:+.2f} pts")
print(f"  Median/signal  : {statistics.median(total_pnls):+.2f} pts")
print(f"  Profit factor  : {pf:.2f}")
print(f"  Avg risk       : {sum(t['total_risk'] for t in trades)/n:.2f} pts")
print()
print(f"  Contract 1 (C-open): avg {sum(c1_pnls)/n:+.2f}  wins {sum(1 for p in c1_pnls if p>0)}/{n}")
if c2_pnls:
    print(f"  Contract 2 (pullback, {len(c2_filled)} fills): avg {sum(c2_pnls)/len(c2_pnls):+.2f}  wins {sum(1 for p in c2_pnls if p>0)}/{len(c2_pnls)}")

# Distribution
cuts = [('<−10',None,-10),('−10→−5',-10,-5),('−5→0',-5,0),
        ('0→+5',0,5),('+5→+10',5,10),('+10→+20',10,20),('>+20',20,None)]
print(f"\n  P&L distribution (GC pts per signal):")
for lbl,lo,hi in cuts:
    c = sum(1 for p in total_pnls if (lo is None or p>lo) and (hi is None or p<=hi))
    if not c: continue
    print(f"    {lbl:>12}  {'█'*c:<28}  {c:>2} ({100*c//n}%)")

# Per-trade detail
print(f"\n  Per-signal detail:")
print(f"  {'Date':<12} {'ONH':>8} {'POC':>8} {'C1@':>8} {'C2@':>8} {'Stop':>8} {'Exit':>8} {'C1':>7} {'C2':>7} {'Total':>8} {'Cts'}")
print(f"  {'─'*96}")
for t in sorted(trades, key=lambda x: x['date']):
    c2e = f"{t['c2_entry']:.2f}" if t['c2_filled'] else '—'
    c2p = f"{t['c2_pnl']:+.2f}" if t['c2_filled'] else '—'
    flg = ' STOP' if t['stopped'] else ''
    print(f"  {t['date']:<12} {t['on_high']:>8.2f} {t['on_poc']:>8.2f} {t['c1_entry']:>8.2f} "
          f"{c2e:>8} {t['on_poc']:>8.2f} {t['exit_px']:>8.2f} "
          f"{t['c1_pnl']:>+7.2f} {c2p:>7} {t['total_pnl']:>+8.2f} {t['contracts']:>4}{flg}")

# ── ES comparison reminder ────────────────────────────────────────────────────
print(f"\n{'═'*64}")
print(f"  ES (same strategy, LONG only, for comparison):")
print(f"    32 signals | +227.00 pts | avg +7.09 | 66% win | PF 1.54")
print(f"{'═'*64}")
