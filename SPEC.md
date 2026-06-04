# doMyTrade — Market Profile Signal Spec

Use this document to validate any live `/api/market-profile/{symbol}` output by hand.
Work through each section in order. Every number here is sourced directly from `backend/main.py`.

---

## 1. Instruments & Tick Sizes

| Symbol | Tick  |
|--------|-------|
| /ES    | 0.25  |
| /NQ    | 0.25  |
| /YM    | 1.00  |
| /RTY   | 0.10  |
| /CL    | 0.01  |
| /GC    | 0.10  |
| /ZB    | 0.03125 |

`straddle_t = 3 × tick` (used in zone and POC straddle checks throughout)

---

## 2. Level Hierarchy (priority order, high → low)

```
Prior RTH VAH / VAL / POC        ← weekly context, extension targets
─────────────────────────────
Overnight High (ONH)             ← IB excess confirmation boundary
Overnight POC (ON POC)           ← pivot / zone fulcrum
Overnight Low (ONL)              ← IB excess confirmation boundary
─────────────────────────────
IB High / IB Low                 ← CONFIRMED zone boundary
─────────────────────────────
Prior RTH VAH / VAL              ← opening type reference
```

**Levels used by each system:**

| System         | Levels consumed |
|----------------|-----------------|
| Opening type   | Prior VAH, Prior VAL (IB H/L for boundary) |
| IB Score       | ONH, ONL, ON POC, overnight inventory direction |
| Zone           | IB Low/High, ONH/ONL, ON POC |
| 80% Rule       | Prior VAH, Prior VAL |
| Live Read      | ONH, ON POC, ONL (zone boundaries) |
| Extension targets | Prior VAH (bullish), Prior VAL (bearish) |

---

## 3. Opening Type (classified at IB completion — uses IB H/L, not A-period alone)

**Inputs:** `open_price`, `ib_high`, `ib_low`, `prior_vah`, `prior_val`, `tick`

```
open inside prior VA  →  OA  (Open Auction)
open ABOVE prior VAH:
  prior_vah ≤ ib_low ≤ prior_vah + 2t  →  OTD ↑  (tested VAH, drove higher)
  ib_low < prior_vah                   →  ORR ↓  (reversed back through VAH)
  otherwise                            →  OD  ↑  (drove away, never tested)
open BELOW prior VAL:
  prior_val - 2t ≤ ib_high ≤ prior_val →  OTD ↓  (tested VAL, drove lower)
  ib_high > prior_val                  →  ORR ↑  (reversed back through VAL)
  otherwise                            →  OD  ↓  (drove away, never tested)
```

**Validation check:**
- [ ] Is open above/below/inside prior VAH/VAL?
- [ ] Did IB High/Low cross the prior VA boundary or just approach it?
- [ ] OTD requires the test to stay within 2 ticks of the boundary, not cross it.

---

## 4. IB Score (fixed at 10:30 AM — never changes intraday)

Score range: −4 to +4. Each pillar is assessed independently.

### P0 — Open vs Overnight Range  (score: −1 / 0 / +1)
```
open_price > ONH + tick  →  +1  (open above overnight range)
open_price < ONL − tick  →  −1  (open below overnight range)
ONL − tick ≤ open ≤ ONH + tick  →  0  (OA open, inside range)
```

### P1 — IB vs ONH/ONL  (score: −2/−1/0/+1/+2)
```
IB High > ONH + tick  AND  IB Low inside overnight range:
  B close > ONH + tick   →  +2  (accepted above ONH)
  B close ≤ ONH + tick   →  +1  (probe above ONH, rejected — OA context)

IB Low < ONL − tick  AND  IB High inside overnight range:
  B close < ONL − tick   →  −2  (accepted below ONL)
  B close ≥ ONL − tick   →  −1  (probe below ONL, rejected — OA context)

IB High > ONH + tick  AND  IB Low < ONL − tick  →  0  (absorbed entire ON range)
IB fully inside overnight range               →  0  (rotational)
```
*Key: For OA opens, score is +2/−2 only if B CLOSES outside. A probe that B closes back inside scores ±1.*

### P2 — IB vs ON POC  (score: −1 / 0 / +1)
```
IB Low > ON POC + tick   →  +1  (entire IB above ON POC)
IB High < ON POC − tick  →  −1  (entire IB below ON POC)
IB straddles ON POC:
  B close > ON POC + straddle_t  →  +1  (ON POC held as support)
  B close < ON POC − straddle_t  →  −1  (ON POC acted as resistance)
  |B close − ON POC| ≤ straddle_t →   0  (genuine straddle — indecision)
```

### P3 — Overnight Inventory  (score: −1 / 0 / +1)
```
ON trended UP   AND  IB Low > ON POC  →  +1  (longs aligned, IB above POC)
ON trended DOWN AND  IB High < ON POC →  −1  (shorts aligned, IB below POC)
misalignment (ON up but IB below POC, or ON down but IB above POC)  →  0 + CAUTION flag
```
*Overnight trend = compare midpoint of first ON period vs midpoint of last ON period.*

### Score → Bias mapping
```
≥ +4  Strong Bullish
+3    Bullish
+2    Bullish
+1    Bullish Lean
 0    Neutral
−1    Bearish Lean
−2    Bearish
−3    Bearish
≤ −4  Strong Bearish
```

**Validation checklist (run after 10:30 AM):**
- [ ] P0: is open above ONH, below ONL, or inside?
- [ ] P1: did IB High exceed ONH? Did B close above ONH or back inside?
- [ ] P1: did IB Low breach ONL? Did B close below ONL or back inside?
- [ ] P2: is entire IB above / below ON POC? If straddle, where did B close?
- [ ] P3: did overnight trend up? Is IB fully above ON POC?
- [ ] Sum P0+P1+P2+P3. Does it match `ib_signals.ib_score`?

---

## 5. Zone System (live — updates every period close)

### Zone boundaries (bearish IB — mirror for bullish)

```
CONFIRMED   : close < IB Low − tick           (sellers extending below IB)
INTACT      : close ≤ ONL + straddle_t        (below ONL, excess holding)
WEAKENING   : close < ON POC − straddle_t     (above ONL, below ON POC)
CRITICAL    : close ≤ ON POC + straddle_t     (at ON POC ± 3 ticks)
INVALIDATED : close > ON POC + straddle_t     (above ON POC)
```

### Zone boundaries (bullish IB — mirror)
```
CONFIRMED   : close > IB High + tick
INTACT      : close ≥ ONH − straddle_t
WEAKENING   : close > ON POC + straddle_t
CRITICAL    : close ≥ ON POC − straddle_t
INVALIDATED : close < ON POC − straddle_t
```

### Downgrade rule (prevents noise flips)
- Downgrades require **two consecutive period closes** in the new zone.
- First close in a worse zone = **WARNING** badge held at previous zone, `first_warning = True`.
- Upgrades (recovering toward CONFIRMED) are **immediate** — no two-close requirement.
- Exception: if a single bar jumps **two zones at once** (e.g. WEAKENING → INVALIDATED, skipping CRITICAL), the first_warning fires with the correct "first close above/below ON POC" text, not the ONL/ONH text.

### Zone → Live adjustment
```
Direction = +1 if IB bullish,  −1 if IB bearish

CONFIRMED   → direction × +2   (accelerating)
INTACT      → direction ×  0   (holding)
WEAKENING   → direction × −1   (fading)
CRITICAL    → direction × −2   (last defence)
INVALIDATED → direction × −3   (flip)
```

### Live score = IB score + zone adjustment
```
Example (today's /NQ):
  IB score = −3  (Bearish)
  Zone = WEAKENING  →  adjustment = (−1) × (−1) = +1
  Live score = −3 + 1 = −2  (Bearish)
```

**Validation checklist (run each period close):**
- [ ] Where did the period close relative to IB Low/High, ONL/ONH, ON POC?
- [ ] Which zone does that close land in?
- [ ] Is this the same zone as last period (confirmed) or different (first_warning)?
- [ ] Apply direction × zone_adj. Does it match `live_read.live_adjustment`?
- [ ] IB score + adj = live score. Does it match `live_read.current_score`?

---

## 6. WEAKENING Zone — Narrative Sub-states

The WEAKENING zone spans the entire ONL → ON POC corridor (bearish) or ON POC → ONH (bullish). The narrative shifts based on position within that range:

```
BEARISH WEAKENING:
  first close above ONL (first_warning, close < ON POC):
      "first test of ONL as support — one period is noise"
  first close that jumped past CRITICAL into INVALIDATED (first_warning, close > ON POC + straddle_t):
      "first close above ON POC — signal approaching invalidation"
  two closes confirmed, close < 70% of ONL→ON POC range:
      "ONL failed as resistance — OA two-sided behaviour active"
  two closes confirmed, close ≥ 70% of ONL→ON POC range:
      "approaching ON POC — sellers must hold as resistance"
```

**70% threshold calculation (bearish):**
```
wk_range = ON POC − ONL
pct = (last_close − ONL) / wk_range
narrative shifts at pct ≥ 0.70
```

---

## 7. INVALIDATED Zone — Confirmed State

When two consecutive closes confirm INVALIDATED, the level roles flip:

```
Bearish INVALIDATED:  ON POC → support,  ONH → next resistance  (Watch: ONH)
Bullish INVALIDATED:  ON POC → resistance, ONL → next support   (Watch: ONL)
```

---

## 8. Day Type (classified after IB, updates intraday)

**Inputs:** `session_high`, `session_low`, `ib_high`, `ib_low`, `ib_range`, `prior_rth_range`

```
ext_up   = session_high − ib_high   (upside extension beyond IB)
ext_down = ib_low − session_low     (downside extension beyond IB)
total_range = session_high − session_low
ib_ratio = ib_range / prior_rth_range
```

| Condition | Day Type |
|-----------|----------|
| total_range > 2.5 × IB AND one side > 1.5 × IB | Trend Day ↑/↓ |
| Both sides extended AND skew > 35% of IB | Neutral Extreme ↑/↓ |
| Both sides extended | Neutral Day |
| ext_up > 25% of IB AND ext_up > ext_down | Normal Variation ↑ |
| ext_down > 25% of IB AND ext_down > ext_up | Normal Variation ↓ |
| Otherwise (wide IB, balanced) | Normal Day |

**Validation checklist:**
- [ ] Calculate ext_up and ext_down from session_high/low vs IB.
- [ ] Check both-sided extensions.
- [ ] Check total_range vs 2.5 × IB for Trend Day.

---

## 9. 80% Rule

**Triggers when:** open inside prior VA AND A period inside VA AND B period inside VA.

```
open in upper half of VA (open ≥ mid_va)  →  SHORT target: Prior VAL
open in lower half of VA (open < mid_va)  →  LONG  target: Prior VAH
```

**Validation checklist:**
- [ ] Is open inside prior VAH/VAL?
- [ ] Did A period stay inside prior VA (no breach)?
- [ ] Did B period stay inside prior VA (no breach)?
- [ ] Is open above or below the VA midpoint?

---

## 10. Full Validation Walkthrough (step by step)

Use this after any session's IB closes (10:30 AM ET):

```
Step 1 — Collect levels
  prior_vah, prior_val, prior_poc
  ONH, ONL, ON POC, ON VAH, ON VAL
  IB High, IB Low, B close
  open_price

Step 2 — Opening type
  Apply section 3 logic → OA / OD / OTD / ORR

Step 3 — IB Score
  Score P0 + P1 + P2 + P3 → total
  Confirm vs API: ib_signals.ib_score

Step 4 — Zone (after each period close)
  Locate close vs IB Low/High, ONL/ONH, ON POC
  Apply section 5 zone boundaries → zone name
  Apply first_warning rule (two consecutive)
  Apply zone adj → live_adjustment
  IB score + adj → current_score
  Confirm vs API: live_read.{status, live_adjustment, current_score}

Step 5 — Narrative sub-state
  If WEAKENING: check 70% threshold and first_warning jump check (section 6)
  If INVALIDATED confirmed: check watch level flipped to ONH/ONL (section 7)

Step 6 — Day type
  Apply section 8 conditions → type label
  Confirm vs API: day_type.type

Step 7 — 80% Rule
  Apply section 9 conditions
  Confirm vs API: rule_80.triggered
```

---

## 11. Common Bug Patterns (from production incidents)

| Bug | Root cause | Detection |
|-----|-----------|-----------|
| ORR when price never re-entered VA | Was checking `open ± 4t` instead of VA boundary | Step 2: verify ib_low/high vs prior_vah/val |
| Opening type wrong (OD when should be ORR) | Was using A-period alone, not IB (A+B) | Step 2: use `today.ib_high/ib_low`, not `period_ranges.A` |
| "First test of ONL" when 300pts above ONL | Close jumped WEAKENING→INVALIDATED in one bar; first_warning fired wrong branch | Step 4: if first_warning AND close > ON POC + straddle_t → use INVALIDATED text |
| WEAKENING narrative stale near ON POC | Single static text for entire ONL→POC corridor | Step 5: check 70% threshold |
| INVALIDATED confirmed didn't mention ONH | Text only said "ON POC is support", missed next target | Step 7: watch level should be ONH/ONL, not ON POC |
