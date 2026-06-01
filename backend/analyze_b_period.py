"""
Analyse ES B-period (10:00–10:30 AM ET) vs next hour (10:30–11:30 AM ET)
over all available 30-min data (~260 days).

Usage:
    python3 analyze_b_period.py
"""
from dotenv import load_dotenv
load_dotenv('/Users/wassim/domytrade/backend/.env')

from db import get_db
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict
import statistics

ET = ZoneInfo('America/New_York')

# ── Paginate all 30-min ES bars ──────────────────────────────────────────────
print("Fetching 30-min ES bars (paginated)…")
PAGE  = 1000
bars  = []
start = 0
while True:
    res = (get_db().table('ohlc_30min')
           .select('bar_time,open,high,low,close,volume')
           .eq('symbol_id', 5)
           .order('bar_time')
           .range(start, start + PAGE - 1)
           .execute())
    chunk = res.data
    bars.extend(chunk)
    if len(chunk) < PAGE:
        break
    start += PAGE

print(f"  {len(bars)} bars  |  "
      f"{bars[0]['bar_time'][:10]}  →  {bars[-1]['bar_time'][:10]}\n")

# ── Bucket bars by ET date and (hour, minute) ────────────────────────────────
day_periods: dict[str, dict] = defaultdict(dict)
for b in bars:
    dt = datetime.fromisoformat(b['bar_time']).astimezone(ET)
    day_periods[dt.strftime('%Y-%m-%d')][(dt.hour, dt.minute)] = b

B = (10, 0)    # B period  10:00–10:30 AM
C = (10, 30)   # C period  10:30–11:00 AM
D = (11, 0)    # D period  11:00–11:30 AM

# ── Build per-day rows ────────────────────────────────────────────────────────
rows = []
for date, periods in sorted(day_periods.items()):
    b_bar = periods.get(B)
    c_bar = periods.get(C)
    d_bar = periods.get(D)
    if not b_bar or not c_bar or not d_bar:
        continue

    b_move  = round(b_bar['close'] - b_bar['open'], 2)
    b_range = round(b_bar['high']  - b_bar['low'],  2)

    # Next hour: C open → D close
    nh_move  = round(d_bar['close'] - c_bar['open'], 2)
    nh_high  = max(c_bar['high'], d_bar['high'])
    nh_low   = min(c_bar['low'],  d_bar['low'])
    nh_range = round(nh_high - nh_low, 2)

    rows.append({
        'date':     date,
        'b_move':   b_move,
        'b_range':  b_range,
        'b_up':     b_move > 0,
        'nh_move':  nh_move,
        'nh_range': nh_range,
        'nh_up':    nh_move > 0,
    })

print(f"Complete RTH days with B + C + D periods: {len(rows)}\n")

up_days   = [r for r in rows if r['b_up']]
down_days = [r for r in rows if not r['b_up']]


def pct_bar(count, total, width=35):
    filled = int(round(width * count / total)) if total else 0
    return '▓' * filled


def analyse(days: list, label: str, expect_up: bool) -> None:
    n = len(days)
    if not n:
        print(f"No data for: {label}"); return

    b_moves  = [r['b_move']  for r in days]
    b_ranges = [r['b_range'] for r in days]
    nh_moves = [r['nh_move'] for r in days]
    nh_rngs  = [r['nh_range'] for r in days]

    continuation = sum(1 for r in days if r['nh_up'] == expect_up)
    reversal     = n - continuation

    sorted_days = sorted(days, key=lambda r: r['nh_move'])

    print(f"{'═'*64}")
    print(f"  {label}")
    print(f"  n = {n} days  |  "
          f"{sorted_days[0]['date']} → {sorted_days[-1]['date']}")
    print(f"{'═'*64}")
    print(f"  B period avg move     : {sum(b_moves)/n:+.2f} pts")
    print(f"  B period avg range    : {sum(b_ranges)/n:.2f} pts")
    print()
    print(f"  Next-hour avg move    : {sum(nh_moves)/n:+.2f} pts")
    print(f"  Next-hour median move : {statistics.median(nh_moves):+.2f} pts")
    print(f"  Next-hour avg range   : {sum(nh_rngs)/n:.2f} pts")
    print(f"  Next-hour max move    : {max(nh_moves):+.2f} pts")
    print(f"  Next-hour min move    : {min(nh_moves):+.2f} pts")
    print()
    print(f"  Continuation (same dir as B) : {continuation:>3}/{n}  ({100*continuation/n:.0f}%)")
    print(f"  Reversal     (opposite dir)  : {reversal:>3}/{n}  ({100*reversal/n:.0f}%)")
    print()

    buckets = [
        ('<−20',     None, -20),
        ('−20→−10',  -20,  -10),
        ('−10→−5',   -10,   -5),
        ('−5→0',      -5,    0),
        ('0→+5',       0,    5),
        ('+5→+10',     5,   10),
        ('+10→+20',   10,   20),
        ('>+20',      20,  None),
    ]
    print(f"  Distribution of next-hour move (ES pts):")
    for lbl, lo, hi in buckets:
        count = sum(1 for m in nh_moves
                    if (lo is None or m > lo) and (hi is None or m <= hi))
        print(f"    {lbl:>12}  {pct_bar(count,n):<36} {count:>3}  ({100*count/n:.0f}%)")
    print()

    # Flag big outliers (> 3× median absolute deviation)
    med  = statistics.median([abs(m) for m in nh_moves])
    outliers = [r for r in days if abs(r['nh_move']) > max(3*med, 20)]
    if outliers:
        print(f"  ** OUTLIER DAYS (|move| > 3× MAD or > 20 pts) — "
              f"likely news events:")
        for r in sorted(outliers, key=lambda r: r['nh_move']):
            print(f"    {r['date']}  B {r['b_move']:+6.2f}  →  next {r['nh_move']:+7.2f}  "
                  f"range {r['nh_range']:.2f}")
        print()

    print(f"  ── 5 largest DROPS in next hour ──")
    for r in sorted_days[:5]:
        print(f"    {r['date']}  B {r['b_move']:+6.2f}  →  next {r['nh_move']:+7.2f}  "
              f"range {r['nh_range']:.2f}")
    print()
    print(f"  ── 5 largest RALLIES in next hour ──")
    for r in sorted_days[-5:]:
        print(f"    {r['date']}  B {r['b_move']:+6.2f}  →  next {r['nh_move']:+7.2f}  "
              f"range {r['nh_range']:.2f}")
    print()


analyse(up_days,   "CASE 1 — B PERIOD WENT UP   (10:00–10:30 closed BULLISH)", expect_up=True)
analyse(down_days, "CASE 2 — B PERIOD DIPPED     (10:00–10:30 closed BEARISH)", expect_up=False)

# ── Conviction split ─────────────────────────────────────────────────────────
FLAT = 2.5
big_up   = [r for r in up_days   if r['b_move'] >  FLAT]
small_up = [r for r in up_days   if r['b_move'] <= FLAT]
big_dn   = [r for r in down_days if r['b_move'] < -FLAT]
small_dn = [r for r in down_days if r['b_move'] >= -FLAT]

print(f"{'═'*64}")
print(f"  CONVICTION SPLIT  (threshold ±{FLAT} pts)")
print(f"{'═'*64}")
for grp, lbl, exp_up in [
    (big_up,   'STRONG B UP  (> +2.5 pts)', True),
    (small_up, 'WEAK   B UP  (0 → +2.5)  ', True),
    (small_dn, 'WEAK   B DN  (−2.5 → 0)  ', False),
    (big_dn,   'STRONG B DN  (< −2.5 pts)', False),
]:
    n = len(grp)
    if not n: continue
    nh = [r['nh_move'] for r in grp]
    cont = sum(1 for r in grp if r['nh_up'] == exp_up)
    print(f"  {lbl}  n={n:>3}  "
          f"avg {sum(nh)/n:+.2f}  "
          f"med {statistics.median(nh):+.2f}  "
          f"cont {cont}/{n} ({100*cont/n:.0f}%)")
print()

# ── Overall ──────────────────────────────────────────────────────────────────
total  = len(rows)
all_nh = [r['nh_move'] for r in rows]
print(f"{'═'*64}")
print(f"  TOTAL SUMMARY   {rows[0]['date']} → {rows[-1]['date']}")
print(f"{'═'*64}")
print(f"  Trading days  : {total}")
print(f"  B UP days     : {len(up_days)} ({100*len(up_days)/total:.0f}%)")
print(f"  B DOWN days   : {len(down_days)} ({100*len(down_days)/total:.0f}%)")
print(f"  Baseline next-hour avg : {sum(all_nh)/total:+.2f}  "
      f"median {statistics.median(all_nh):+.2f} pts")
