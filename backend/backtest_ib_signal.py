"""
Backtest: OA Open + B Accepted — Blended 1+1 Contract Entry

Contract 1:  Enter at C period open immediately (never miss a trend day)
Contract 2:  Enter on pullback to ONH/ONL (better average, if it comes)
Stop:        ON POC for both contracts
Exit:        Stop hit, or session close (M period close)

Compared against:
  A) 1 contract — pullback only
  B) 1 contract — C open only
  C) 1+1 blend  — C open + pullback (the new strategy)
"""
from dotenv import load_dotenv
load_dotenv('/Users/wassim/domytrade/backend/.env')

from db import get_db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import statistics

ET   = ZoneInfo('America/New_York')
TICK = 0.25

# ── Fetch 30-min ES bars ──────────────────────────────────────────────────────
print("Fetching bars…")
bars  = []
start = 0
while True:
    res = (get_db().table('ohlc_30min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', 5).order('bar_time')
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


# ── Core simulation ───────────────────────────────────────────────────────────
def simulate_blend(direction, c_open, pullback_lvl, stop_lvl, pr, c_hm):
    """
    Simulate 1+1 blend starting from C period open.
    Contract 1: entered at c_open immediately.
    Contract 2: entered at pullback_lvl if price touches it before stop.
    Stop for both: stop_lvl.
    Returns dict with full breakdown.
    """
    post = [l for hm, l in sorted(RTH_LETTERS.items()) if hm > c_hm]

    c1_entry   = c_open
    c2_entry   = None       # filled when pullback reached
    c2_ltr     = None
    exit_px    = None
    exit_ltr   = None
    stopped    = False
    mfe_c1     = 0.0
    mfe_c2     = 0.0

    for ltr in post:
        bar = pr.get(ltr)
        if bar is None:
            continue

        if direction == 'LONG':
            # Check stop first (conservative — if both levels in same bar, stop wins)
            if bar['low'] <= stop_lvl:
                stopped  = True
                exit_px  = stop_lvl
                exit_ltr = ltr
                # Update MFE before stopping
                mfe_c1 = max(mfe_c1, bar['high'] - c1_entry)
                if c2_entry:
                    mfe_c2 = max(mfe_c2, bar['high'] - c2_entry)
                break
            # Fill contract 2 on pullback (only if not yet filled)
            if c2_entry is None and bar['low'] <= pullback_lvl + TICK:
                c2_entry = pullback_lvl
                c2_ltr   = ltr
            # Update MFE
            mfe_c1 = max(mfe_c1, bar['high'] - c1_entry)
            if c2_entry:
                mfe_c2 = max(mfe_c2, bar['high'] - c2_entry)

        else:  # SHORT
            if bar['high'] >= stop_lvl:
                stopped  = True
                exit_px  = stop_lvl
                exit_ltr = ltr
                mfe_c1 = max(mfe_c1, c1_entry - bar['low'])
                if c2_entry:
                    mfe_c2 = max(mfe_c2, c2_entry - bar['low'])
                break
            if c2_entry is None and bar['high'] >= pullback_lvl - TICK:
                c2_entry = pullback_lvl
                c2_ltr   = ltr
            mfe_c1 = max(mfe_c1, c1_entry - bar['low'])
            if c2_entry:
                mfe_c2 = max(mfe_c2, c2_entry - bar['low'])

    if not stopped:
        last = pr.get('M') or pr.get('L') or pr.get('K')
        if last:
            exit_px  = last['close']
            exit_ltr = RTH_LETTERS.get(last['hm'], '?')

    if exit_px is None:
        return None

    pnl_c1 = (exit_px - c1_entry) if direction == 'LONG' else (c1_entry - exit_px)
    pnl_c2 = ((exit_px - c2_entry) if direction == 'LONG' else (c2_entry - exit_px)) if c2_entry else None

    risk_c1 = abs(c1_entry   - stop_lvl)
    risk_c2 = abs(c2_entry   - stop_lvl) if c2_entry else None
    total_risk = risk_c1 + (risk_c2 or 0)
    total_pnl  = pnl_c1 + (pnl_c2 or 0)

    return {
        'stopped':    stopped,
        'exit_px':    exit_px,
        'exit_ltr':   exit_ltr,
        # C1 (C-open)
        'c1_entry':   c1_entry,
        'c1_pnl':     round(pnl_c1, 2),
        'c1_risk':    round(risk_c1, 2),
        'c1_mfe':     round(mfe_c1, 2),
        # C2 (pullback) — None if never filled
        'c2_filled':  c2_entry is not None,
        'c2_entry':   c2_entry,
        'c2_ltr':     c2_ltr,
        'c2_pnl':     round(pnl_c2, 2) if pnl_c2 is not None else None,
        'c2_risk':    round(risk_c2, 2) if risk_c2 is not None else None,
        'c2_mfe':     round(mfe_c2, 2) if c2_entry else None,
        # Totals (sum of both contracts)
        'total_pnl':  round(total_pnl, 2),
        'total_risk': round(total_risk, 2),
        'contracts':  2 if c2_entry else 1,
    }


# ── Main loop ─────────────────────────────────────────────────────────────────
blend_trades   = []
pullback_only  = []   # 1-contract pullback (Entry A, for comparison)
copen_only     = []   # 1-contract C-open   (Entry B, for comparison)

all_dates = sorted(set(b['date'] for b in bars))

for date in all_dates:
    on = overnight_levels(date)
    pr = rth_periods(date)
    if not on or not all(k in pr for k in ('A','B','C')):
        continue

    on_high, on_low, on_poc = on['high'], on['low'], on['poc']
    open_px = pr['A']['open']

    if not (on_low - TICK <= open_px <= on_high + TICK):
        continue

    b_close = pr['B']['close']
    b_long  = b_close > on_high + TICK
    b_short = b_close < on_low  - TICK
    if not b_long and not b_short:
        continue
    if b_long and b_short:
        continue

    direction   = 'LONG'  if b_long  else 'SHORT'
    if direction == 'SHORT':
        continue   # LONG signals only
    stop_lvl    = on_poc
    pullback_lvl= on_high if direction == 'LONG' else on_low

    if direction == 'LONG'  and stop_lvl >= pullback_lvl: continue
    if direction == 'SHORT' and stop_lvl <= pullback_lvl: continue

    c_bar  = pr['C']
    c_open = c_bar['open']
    c_hm   = c_bar['hm']

    if direction == 'LONG'  and c_open <= stop_lvl: continue
    if direction == 'SHORT' and c_open >= stop_lvl: continue

    # ── 1+1 Blend ────────────────────────────────────────────────────────────
    t = simulate_blend(direction, c_open, pullback_lvl, stop_lvl, pr, c_hm)
    if t:
        blend_trades.append({'date': date, 'direction': direction,
                              'pullback_lvl': pullback_lvl, 'stop_lvl': stop_lvl, **t})

    # ── 1-contract pullback (Entry A) ─────────────────────────────────────────
    for ltr in ('C', 'D'):
        bar = pr.get(ltr)
        if bar is None: continue
        hit = ((direction == 'LONG'  and bar['low']  <= pullback_lvl + TICK) or
               (direction == 'SHORT' and bar['high'] >= pullback_lvl - TICK))
        if hit:
            post = [l for hm, l in sorted(RTH_LETTERS.items()) if hm > bar['hm']]
            stopped = False; exit_px = None; exit_ltr = None
            for pl in post:
                pb = pr.get(pl)
                if pb is None: continue
                if direction == 'LONG'  and pb['low']  <= stop_lvl:
                    stopped=True; exit_px=stop_lvl; exit_ltr=pl; break
                if direction == 'SHORT' and pb['high'] >= stop_lvl:
                    stopped=True; exit_px=stop_lvl; exit_ltr=pl; break
            if not stopped:
                lb = pr.get('M') or pr.get('L')
                if lb: exit_px=lb['close']; exit_ltr=RTH_LETTERS.get(lb['hm'],'?')
            if exit_px:
                pnl = (exit_px-pullback_lvl) if direction=='LONG' else (pullback_lvl-exit_px)
                pullback_only.append({'date':date,'direction':direction,
                    'pnl_pts':round(pnl,2),'risk_pts':round(abs(pullback_lvl-stop_lvl),2),
                    'stopped':stopped})
            break

    # ── 1-contract C-open (Entry B) ───────────────────────────────────────────
    post = [l for hm,l in sorted(RTH_LETTERS.items()) if hm > c_hm]
    stopped=False; exit_px=None; exit_ltr=None
    for pl in post:
        pb = pr.get(pl)
        if pb is None: continue
        if direction=='LONG'  and pb['low']  <= stop_lvl:
            stopped=True; exit_px=stop_lvl; exit_ltr=pl; break
        if direction=='SHORT' and pb['high'] >= stop_lvl:
            stopped=True; exit_px=stop_lvl; exit_ltr=pl; break
    if not stopped:
        lb = pr.get('M') or pr.get('L')
        if lb: exit_px=lb['close']; exit_ltr=RTH_LETTERS.get(lb['hm'],'?')
    if exit_px:
        pnl = (exit_px-c_open) if direction=='LONG' else (c_open-exit_px)
        copen_only.append({'date':date,'direction':direction,
            'pnl_pts':round(pnl,2),'risk_pts':round(abs(c_open-stop_lvl),2),
            'stopped':stopped})


# ── Results ───────────────────────────────────────────────────────────────────
def report1(trades, label):
    """Report for 1-contract strategies."""
    n = len(trades)
    if not n: return
    pnls  = [t['pnl_pts'] for t in trades]
    risks = [t['risk_pts'] for t in trades]
    wins  = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in trades if t['stopped'])
    gw = sum(p for p in pnls if p>0)
    gl = sum(p for p in pnls if p<0)
    pf = gw/abs(gl) if gl else float('inf')
    print(f"\n{'═'*62}")
    print(f"  {label}  (n={n} trades, 1 contract)")
    print(f"{'═'*62}")
    print(f"  Win rate      : {wins}/{n}  ({100*wins/n:.0f}%)")
    print(f"  Stop-out rate : {stops}/{n}  ({100*stops/n:.0f}%)")
    print(f"  Total P&L     : {sum(pnls):+.2f} pts")
    print(f"  Avg trade     : {sum(pnls)/n:+.2f} pts")
    print(f"  Median trade  : {statistics.median(pnls):+.2f} pts")
    print(f"  Profit factor : {pf:.2f}")
    print(f"  Avg risk      : {sum(risks)/n:.2f} pts")
    by_dir = defaultdict(list)
    for t in trades: by_dir[t['direction']].append(t['pnl_pts'])
    for d, ps in sorted(by_dir.items()):
        w = sum(1 for p in ps if p>0)
        print(f"  {d:<5} {len(ps):>2} trades: {sum(ps):+.2f} pts  ({w}/{len(ps)} wins)")


def report_blend(trades):
    """Report for 1+1 blend strategy."""
    n = len(trades)
    if not n: return
    total_pnls = [t['total_pnl'] for t in trades]
    total_risks= [t['total_risk'] for t in trades]
    c1_pnls    = [t['c1_pnl'] for t in trades]
    c2_pnls    = [t['c2_pnl'] for t in trades if t['c2_filled']]
    wins_total = sum(1 for p in total_pnls if p > 0)
    stops      = sum(1 for t in trades if t['stopped'])
    two_ct     = sum(1 for t in trades if t['c2_filled'])
    one_ct     = n - two_ct
    gw = sum(p for p in total_pnls if p>0)
    gl = sum(p for p in total_pnls if p<0)
    pf = gw/abs(gl) if gl else float('inf')

    print(f"\n{'═'*62}")
    print(f"  ENTRY C — 1+1 Blend (C-open + pullback)  (n={n} signals)")
    print(f"{'═'*62}")
    print(f"  Signal days          : {n}")
    print(f"  — Got 2 contracts    : {two_ct} days ({100*two_ct/n:.0f}%) — pullback came")
    print(f"  — Got 1 contract only: {one_ct} days ({100*one_ct/n:.0f}%) — market ran, no pullback")
    print()
    print(f"  Win rate (combined P&L > 0) : {wins_total}/{n}  ({100*wins_total/n:.0f}%)")
    print(f"  Stop-out rate               : {stops}/{n}  ({100*stops/n:.0f}%)")
    print(f"  Total P&L (both contracts)  : {sum(total_pnls):+.2f} pts")
    print(f"  Avg P&L per signal          : {sum(total_pnls)/n:+.2f} pts")
    print(f"  Median P&L per signal       : {statistics.median(total_pnls):+.2f} pts")
    print(f"  Profit factor               : {pf:.2f}")
    print(f"  Avg total risk per signal   : {sum(total_risks)/n:.2f} pts")
    print()
    print(f"  Contract 1 (C-open) stats:")
    print(f"    Avg P&L : {sum(c1_pnls)/n:+.2f} pts")
    print(f"    Wins    : {sum(1 for p in c1_pnls if p>0)}/{n}  ({100*sum(1 for p in c1_pnls if p>0)/n:.0f}%)")
    if c2_pnls:
        print(f"  Contract 2 (pullback) stats — {two_ct} fills:")
        print(f"    Avg P&L : {sum(c2_pnls)/len(c2_pnls):+.2f} pts")
        print(f"    Wins    : {sum(1 for p in c2_pnls if p>0)}/{len(c2_pnls)}  ({100*sum(1 for p in c2_pnls if p>0)/len(c2_pnls):.0f}%)")

    by_dir = defaultdict(list)
    for t in trades: by_dir[t['direction']].append(t['total_pnl'])
    print()
    for d, ps in sorted(by_dir.items()):
        w = sum(1 for p in ps if p>0)
        print(f"  {d:<5} {len(ps):>2} signals: {sum(ps):+.2f} pts  ({w}/{len(ps)} wins)")

    # Distribution of combined P&L
    cuts = [('<−40',None,-40),('−40→−20',-40,-20),('−20→0',-20,0),
            ('0→+10',0,10),('+10→+20',10,20),('+20→+40',20,40),('>+40',40,None)]
    print(f"\n  Combined P&L distribution (pts per signal):")
    for lbl,lo,hi in cuts:
        c = sum(1 for p in total_pnls if (lo is None or p>lo) and (hi is None or p<=hi))
        if not c: continue
        print(f"    {lbl:>12}  {'█'*c:<28}  {c:>2} ({100*c/n:.0f}%)")

    # Day-by-day detail
    print(f"\n  Per-signal detail:")
    print(f"  {'Date':<12} {'Dir':<6} {'C1@':>8} {'C2@':>8} {'Stop':>8} {'Exit':>8} "
          f"{'C1 P&L':>8} {'C2 P&L':>8} {'Total':>8} {'Cts':>4}")
    print(f"  {'─'*86}")
    for t in sorted(blend_trades, key=lambda x: x['date']):
        c2_e   = f"{t['c2_entry']:.2f}"  if t['c2_filled'] else '—'
        c2_pnl = f"{t['c2_pnl']:+.2f}" if t['c2_filled'] else '—'
        flag   = ' STOP' if t['stopped'] else ''
        print(f"  {t['date']:<12} {t['direction']:<6} "
              f"{t['c1_entry']:>8.2f} {c2_e:>8} {t['stop_lvl']:>8.2f} "
              f"{t['exit_px']:>8.2f} {t['c1_pnl']:>+8.2f} {c2_pnl:>8} "
              f"{t['total_pnl']:>+8.2f} {t['contracts']:>4}{flag}")


# ── Summary comparison ────────────────────────────────────────────────────────
report1(pullback_only, "ENTRY A — Pullback only (1 contract)")
report1(copen_only,    "ENTRY B — C-open only  (1 contract)")
report_blend(blend_trades)

print(f"\n{'═'*62}")
print(f"  SUMMARY COMPARISON")
print(f"{'═'*62}")
pa_pnl = sum(t['pnl_pts'] for t in pullback_only)
co_pnl = sum(t['pnl_pts'] for t in copen_only)
bl_pnl = sum(t['total_pnl'] for t in blend_trades)
print(f"  {'Strategy':<35} {'Trades':>7} {'Total P&L':>10} {'Avg/signal':>12}")
print(f"  {'─'*64}")
print(f"  {'A: Pullback 1 contract':<35} {len(pullback_only):>7} {pa_pnl:>+10.2f} "
      f"{pa_pnl/len(pullback_only) if pullback_only else 0:>+12.2f}")
print(f"  {'B: C-open 1 contract':<35} {len(copen_only):>7} {co_pnl:>+10.2f} "
      f"{co_pnl/len(copen_only) if copen_only else 0:>+12.2f}")
print(f"  {'C: Blend 1+1 (C-open + pullback)':<35} {len(blend_trades):>7} {bl_pnl:>+10.2f} "
      f"{bl_pnl/len(blend_trades) if blend_trades else 0:>+12.2f}")
print(f"\n  Note: Entry C total P&L = sum of both contracts combined.")
print(f"  Entry A & B are 1-contract only for fair comparison.")
print(f"  Multiply Entry A/B by 2 to compare on same capital basis:")
pa2 = pa_pnl * 2
co2 = co_pnl * 2
print(f"    A ×2: {pa2:+.2f} pts   B ×2: {co2:+.2f} pts   C: {bl_pnl:+.2f} pts")
