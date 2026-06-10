"""
Market Profile Rule Engine — Jim Dalton Framework
Futures only: /ES, /NQ, /RTY, /YM, /GC, /CL, etc.

Pure functions — no FastAPI, no DB, no Schwab calls.
Each rule takes data dicts and returns a RuleResult.
Scores are intentionally absent — see spec for rationale.

Spec: docs/market_profile_rule_engine_spec.md
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Data contract types (dicts with known keys)
# ─────────────────────────────────────────────────────────────────────────────
#
# overnight   : {high, low, poc, vah, val}
# prior_rth   : {high, low, poc, vah, val, session_high?, session_low?}
# session_prof: {
#     open_price, ib_high, ib_low, ib_range,
#     period_ranges: {A..M: {high, low, close}},
#     profile: [{price, letters, count}],   ← TPO letter map
#     session_high, session_low, periods
# }


@dataclass
class RuleResult:
    rule_id:  str            # 'R01', 'R02', …
    name:     str            # short display name
    state:    str            # machine-readable state string
    detail:   str            # one-line human narrative
    fired:    bool = True    # False when data is missing / rule cannot evaluate


@dataclass
class PhaseResult:
    phase:   int
    ready:   bool
    results: list[RuleResult] = field(default_factory=list)
    message: str = ''        # 'IB not complete' etc.


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

IB_NARROW_PCT     = 0.25    # IB < 25% of prior range → Trend Day likely
IB_BELOW_NORMAL   = 0.60
IB_NORMAL_MAX     = 1.00    # IB > prior range → Wide
STRADDLE_MULT     = 3       # straddle_t = STRADDLE_MULT × tick
OTF_TREND_THRESH  = 3       # consecutive closes needed for OTF confirmation
MIGRATION_PERIODS = 3       # closes needed for value migration signal
NEAR_EXT_PCT      = 0.0025  # 0.25% — "near session extreme"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _st(tick: float) -> float:
    return STRADDLE_MULT * tick


def _pct_in_range(value: float, lo: float, hi: float) -> float:
    """Position of value within [lo, hi] as 0.0–1.0."""
    span = hi - lo
    if span <= 0:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / span))


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _ordered_closes(period_ranges: dict) -> list[tuple[str, float]]:
    """Return completed period closes in session order A, B, C, …"""
    letters = 'ABCDEFGHIJKLM'
    result = []
    for ltr in letters:
        pr = period_ranges.get(ltr, {})
        c = pr.get('close')
        if c is not None:
            result.append((ltr, c))
    return result


def _no_data(rule_id: str, name: str, reason: str = 'insufficient data') -> RuleResult:
    return RuleResult(rule_id=rule_id, name=name,
                      state='NO_DATA', detail=reason, fired=False)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Opening Context  (fires after A period closes at 10:00 ET)
# ─────────────────────────────────────────────────────────────────────────────

def r01_open_type(session_prof: dict, overnight: dict,
                  prior_rth: dict, tick: float) -> RuleResult:
    """
    R01 · Open Type (Dalton)
    OD / OTD / ORR / OA_OUT / OA_IN
    Requires A period to be closed.
    """
    pr   = session_prof.get('period_ranges', {})
    a    = pr.get('A')
    open_px = session_prof.get('open_price')

    if not a or open_px is None:
        return _no_data('R01', 'Open Type', 'A period not yet closed')

    a_high  = a['high']
    a_low   = a['low']
    a_close = a.get('close')
    if a_close is None:
        return _no_data('R01', 'Open Type', 'A period close missing')

    a_range = a_high - a_low
    if a_range <= 0:
        return _no_data('R01', 'Open Type', 'A range is zero')

    a_close_pct = _pct_in_range(a_close, a_low, a_high)

    st = _st(tick)
    on_high  = overnight.get('high')
    on_low   = overnight.get('low')
    on_poc   = overnight.get('poc')
    p_vah    = prior_rth.get('vah')
    p_val    = prior_rth.get('val')

    refs_high = [r for r in [on_high, on_poc, p_vah] if r is not None]
    refs_low  = [r for r in [on_low,  on_poc, p_val] if r is not None]

    # Open Drive — opened and drove straight in one direction, never returned
    drove_up   = a_close_pct > 0.80 and a_low   > open_px - 4 * tick
    drove_down = a_close_pct < 0.20 and a_high  < open_px + 4 * tick
    if drove_up:
        return RuleResult('R01', 'Open Type', 'OD_BULLISH',
                          'Open Drive bullish — A drove straight up, never returned to open')
    if drove_down:
        return RuleResult('R01', 'Open Type', 'OD_BEARISH',
                          'Open Drive bearish — A drove straight down, never returned to open')

    # Open Test Drive — probed a reference then reversed through open
    tested_low_ref  = any(_near(a_low,  r, st * 2) for r in refs_low)
    tested_high_ref = any(_near(a_high, r, st * 2) for r in refs_high)

    if tested_low_ref and a_close_pct > 0.60:
        return RuleResult('R01', 'Open Type', 'OTD_BULLISH',
                          'Open Test Drive bullish — probed low reference, reversed up through open')
    if tested_high_ref and a_close_pct < 0.40:
        return RuleResult('R01', 'Open Type', 'OTD_BEARISH',
                          'Open Test Drive bearish — probed high reference, reversed down through open')

    # Open Rejection Reverse — broke through reference then aggressively reversed
    broke_below_ref = any(a_low < r - st for r in refs_low  if r is not None)
    broke_above_ref = any(a_high > r + st for r in refs_high if r is not None)

    if broke_below_ref and a_close > open_px + st:
        return RuleResult('R01', 'Open Type', 'ORR_BULLISH',
                          'Open Rejection Reverse bullish — broke below reference, aggressively rejected back above open')
    if broke_above_ref and a_close < open_px - st:
        return RuleResult('R01', 'Open Type', 'ORR_BEARISH',
                          'Open Rejection Reverse bearish — broke above reference, aggressively rejected back below open')

    # Open Auction variants
    if on_high is not None and on_low is not None:
        if open_px > on_high + tick or open_px < on_low - tick:
            return RuleResult('R01', 'Open Type', 'OA_OUT_RANGE',
                              f'Open Auction outside overnight range — higher odds of directional move developing')

    return RuleResult('R01', 'Open Type', 'OA_IN_RANGE',
                      'Open Auction inside overnight range — two-sided discovery expected')


def r02_open_vs_prior_va(session_prof: dict, prior_rth: dict) -> RuleResult:
    """
    R02 · Open vs Prior RTH Value Area
    Yesterday's accepted value is the strongest structural reference.
    """
    open_px = session_prof.get('open_price')
    p_high  = prior_rth.get('session_high') or prior_rth.get('high')
    p_low   = prior_rth.get('session_low')  or prior_rth.get('low')
    p_vah   = prior_rth.get('vah')
    p_val   = prior_rth.get('val')

    if open_px is None or p_vah is None or p_val is None:
        return _no_data('R02', 'Open vs Prior VA')

    if p_high and open_px > p_high:
        return RuleResult('R02', 'Open vs Prior VA', 'ABOVE_PRIOR_HIGH',
                          f'Opened above prior session high ({p_high:.2f}) — gap up')
    if open_px > p_vah:
        return RuleResult('R02', 'Open vs Prior VA', 'ABOVE_PRIOR_VAH',
                          f'Opened above prior VAH ({p_vah:.2f}) — buyers accepting higher value')
    if open_px >= p_val:
        return RuleResult('R02', 'Open vs Prior VA', 'INSIDE_PRIOR_VA',
                          f'Opened inside prior value area ({p_val:.2f}–{p_vah:.2f}) — balanced start')
    if p_low and open_px < p_low:
        return RuleResult('R02', 'Open vs Prior VA', 'BELOW_PRIOR_LOW',
                          f'Opened below prior session low ({p_low:.2f}) — gap down')
    return RuleResult('R02', 'Open vs Prior VA', 'BELOW_PRIOR_VAL',
                      f'Opened below prior VAL ({p_val:.2f}) — sellers accepting lower value')


def r03_open_vs_on_poc(session_prof: dict, overnight: dict,
                       tick: float) -> RuleResult:
    """
    R03 · Open vs Overnight POC
    ON POC is the fairest overnight price — opening above/below signals intent.
    """
    open_px = session_prof.get('open_price')
    on_poc  = overnight.get('poc')

    if open_px is None or on_poc is None:
        return _no_data('R03', 'Open vs ON POC')

    if open_px > on_poc + tick:
        return RuleResult('R03', 'Open vs ON POC', 'ABOVE_ON_POC',
                          f'Opened above overnight POC ({on_poc:.2f}) — buyers have slight edge')
    if open_px < on_poc - tick:
        return RuleResult('R03', 'Open vs ON POC', 'BELOW_ON_POC',
                          f'Opened below overnight POC ({on_poc:.2f}) — sellers have slight edge')
    return RuleResult('R03', 'Open vs ON POC', 'AT_ON_POC',
                      f'Opened at overnight POC ({on_poc:.2f}) — perfectly balanced')


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — Initial Balance  (hard gate: 10:30 AM ET)
# ─────────────────────────────────────────────────────────────────────────────

def r04_ib_width(session_prof: dict, prior_rth: dict) -> RuleResult:
    """
    R04 · IB Width
    Narrow IB → Trend Day likely. Wide IB → extremes hold.
    """
    ib_range = session_prof.get('ib_range')
    p_high   = prior_rth.get('session_high') or prior_rth.get('high')
    p_low    = prior_rth.get('session_low')  or prior_rth.get('low')

    if ib_range is None:
        return _no_data('R04', 'IB Width', 'IB not yet complete')
    if p_high is None or p_low is None or p_high == p_low:
        return _no_data('R04', 'IB Width', 'Prior RTH range unavailable')

    prior_range = p_high - p_low
    pct = ib_range / prior_range

    if pct < IB_NARROW_PCT:
        return RuleResult('R04', 'IB Width', 'NARROW',
                          f'IB {ib_range:.2f} = {pct:.0%} of prior range — coiled spring, Trend Day likely')
    if pct < IB_BELOW_NORMAL:
        return RuleResult('R04', 'IB Width', 'BELOW_NORMAL',
                          f'IB {ib_range:.2f} = {pct:.0%} of prior range — Normal Variation Day possible')
    if pct <= IB_NORMAL_MAX:
        return RuleResult('R04', 'IB Width', 'NORMAL',
                          f'IB {ib_range:.2f} = {pct:.0%} of prior range — Normal Day or Normal Variation Day')
    return RuleResult('R04', 'IB Width', 'WIDE',
                      f'IB {ib_range:.2f} = {pct:.0%} of prior range — IB extremes likely hold as day high/low')


def r05_ib_vs_on_range(session_prof: dict, overnight: dict,
                       tick: float) -> RuleResult:
    """
    R05 · IB Position vs Overnight Range
    Core Dalton rule — who is in control: overnight or day-session traders?
    Genuine acceptance (B closed outside) vs probe-and-reject.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    on_high = overnight.get('high')
    on_low  = overnight.get('low')
    b_close = (session_prof.get('period_ranges', {}).get('B') or {}).get('close')

    if any(v is None for v in [ib_high, ib_low, on_high, on_low, b_close]):
        return _no_data('R05', 'IB vs ON Range')

    # Priority order: straddles first, then directional cases
    above_onh = ib_high > on_high + tick
    below_onl = ib_low  < on_low  - tick

    if above_onh and below_onl:
        return RuleResult('R05', 'IB vs ON Range', 'STRADDLES_ON_RANGE',
                          f'IB straddles overnight range — both sides tested, two-sided no net control')
    if above_onh:
        if b_close > on_high:
            return RuleResult('R05', 'IB vs ON Range', 'ACCEPTED_ABOVE_ONH',
                              f'IB extended above ONH ({on_high:.2f}) AND B closed above it — day session buyers in control')
        return RuleResult('R05', 'IB vs ON Range', 'PROBE_ABOVE_ONH',
                          f'IB probed above ONH ({on_high:.2f}) but B closed back inside — overnight sellers held, OA two-sided')
    if below_onl:
        if b_close < on_low:
            return RuleResult('R05', 'IB vs ON Range', 'ACCEPTED_BELOW_ONL',
                              f'IB extended below ONL ({on_low:.2f}) AND B closed below it — day session sellers in control')
        return RuleResult('R05', 'IB vs ON Range', 'PROBE_BELOW_ONL',
                          f'IB probed below ONL ({on_low:.2f}) but B closed back inside — overnight buyers held, OA two-sided')

    return RuleResult('R05', 'IB vs ON Range', 'INSIDE_ON_RANGE',
                      f'IB entirely inside overnight range ({on_low:.2f}–{on_high:.2f}) — overnight traders in control, OA two-sided')


def r06_ib_vs_prior_va(session_prof: dict, prior_rth: dict) -> RuleResult:
    """
    R06 · IB vs Prior RTH Value Area
    Extension beyond yesterday's value confirms directional commitment.
    Returns two sub-states: ib_high_pos and ib_low_pos.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    p_high  = prior_rth.get('session_high') or prior_rth.get('high')
    p_low   = prior_rth.get('session_low')  or prior_rth.get('low')
    p_vah   = prior_rth.get('vah')
    p_val   = prior_rth.get('val')

    if any(v is None for v in [ib_high, ib_low, p_vah, p_val]):
        return _no_data('R06', 'IB vs Prior VA')

    def _high_pos(v):
        if p_high and v > p_high:     return 'ABOVE_PRIOR_HIGH'
        if v > p_vah:                 return 'ABOVE_PRIOR_VAH'
        if v >= p_val:                return 'INSIDE_PRIOR_VA'
        return                               'BELOW_PRIOR_VAL'

    def _low_pos(v):
        if p_low and v < p_low:       return 'BELOW_PRIOR_LOW'
        if v < p_val:                 return 'BELOW_PRIOR_VAL'
        if v <= p_vah:                return 'INSIDE_PRIOR_VA'
        return                               'ABOVE_PRIOR_VAH'

    high_pos = _high_pos(ib_high)
    low_pos  = _low_pos(ib_low)

    parts = []
    if 'ABOVE' in high_pos and high_pos != 'INSIDE_PRIOR_VA':
        parts.append(f'IB high {ib_high:.2f} {high_pos.replace("_", " ").lower()}')
    if 'BELOW' in low_pos and low_pos != 'INSIDE_PRIOR_VA':
        parts.append(f'IB low {ib_low:.2f} {low_pos.replace("_", " ").lower()}')
    if not parts:
        parts = [f'IB contained within prior VA ({p_val:.2f}–{p_vah:.2f})']

    state = f'{high_pos}|{low_pos}'
    return RuleResult('R06', 'IB vs Prior VA', state, ' / '.join(parts))


def r07_on_poc_during_ib(session_prof: dict, overnight: dict,
                         tick: float) -> RuleResult:
    """
    R07 · ON POC Test During IB
    ON POC held = buy zone confirmed. ON POC broken = support failed.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    b_close = (session_prof.get('period_ranges', {}).get('B') or {}).get('close')
    on_poc  = overnight.get('poc')

    if any(v is None for v in [ib_high, ib_low, b_close, on_poc]):
        return _no_data('R07', 'ON POC During IB')

    st = _st(tick)

    if not (ib_low <= on_poc <= ib_high):
        return RuleResult('R07', 'ON POC During IB', 'NOT_TESTED',
                          f'ON POC ({on_poc:.2f}) not inside IB range — level not in play yet')

    if b_close > on_poc + st:
        return RuleResult('R07', 'ON POC During IB', 'TESTED_HELD_ABOVE',
                          f'ON POC ({on_poc:.2f}) tested and buyers defended it — buy zone confirmed')
    if b_close < on_poc - st:
        return RuleResult('R07', 'ON POC During IB', 'TESTED_HELD_BELOW',
                          f'ON POC ({on_poc:.2f}) tested and sellers defended it — resistance confirmed')
    return RuleResult('R07', 'ON POC During IB', 'AT_POC',
                      f'B closed at ON POC ({on_poc:.2f}) — indecisive, watching for next period')


def r08_day_type_estimate(r04: RuleResult, r05: RuleResult,
                          r01: RuleResult) -> RuleResult:
    """
    R08 · Day Type Estimate
    Derived from R04 (width) + R05 (IB vs ON range) + R01 (open type).
    Sets trade plan mode — Trend / Normal Variation / Normal / Neutral / Non-Trend.
    """
    if not r04.fired or not r05.fired:
        return _no_data('R08', 'Day Type Estimate', 'R04 or R05 not ready')

    w  = r04.state
    p  = r05.state
    ot = r01.state if r01.fired else ''

    accepted = p in ('ACCEPTED_ABOVE_ONH', 'ACCEPTED_BELOW_ONL')
    probe    = p in ('PROBE_ABOVE_ONH', 'PROBE_BELOW_ONL')
    inside   = p == 'INSIDE_ON_RANGE'
    straddle = p == 'STRADDLES_ON_RANGE'

    if w == 'NARROW' and accepted:
        return RuleResult('R08', 'Day Type Estimate', 'TREND_DAY',
                          'Narrow IB + accepted outside ON range — Trend Day likely. Size up on first extension, ride it')
    if w == 'NARROW' and probe:
        return RuleResult('R08', 'Day Type Estimate', 'FAILED_TREND_ATTEMPT',
                          'Narrow IB but probe rejected — failed trend attempt. Normal Variation or Normal Day')
    if w == 'NARROW' and inside:
        return RuleResult('R08', 'Day Type Estimate', 'NORMAL_VARIATION_OR_NORMAL',
                          'Narrow IB inside ON range — could break either way. Watch first extension')
    if w in ('NORMAL', 'BELOW_NORMAL') and accepted:
        return RuleResult('R08', 'Day Type Estimate', 'NORMAL_VARIATION_DAY',
                          'Normal IB + accepted outside ON range — Normal Variation Day. One extension, responsive OTF halts it')
    if w in ('NORMAL', 'BELOW_NORMAL') and straddle:
        return RuleResult('R08', 'Day Type Estimate', 'NEUTRAL_DAY',
                          'Normal IB straddles ON range — Neutral Day likely. Fade both extremes, target midpoint')
    if w in ('NORMAL', 'BELOW_NORMAL') and inside:
        return RuleResult('R08', 'Day Type Estimate', 'NORMAL_DAY',
                          'Normal IB inside ON range — Normal Day. Two-sided rotation inside IB')
    if w == 'WIDE':
        return RuleResult('R08', 'Day Type Estimate', 'NORMAL_DAY',
                          'Wide IB — IB extremes likely hold as day high/low. Normal Day, activity stays inside')
    if straddle:
        return RuleResult('R08', 'Day Type Estimate', 'NEUTRAL_DAY',
                          'IB straddles ON range — Neutral Day. Fade both extremes')

    return RuleResult('R08', 'Day Type Estimate', 'UNDETERMINED',
                      'Cannot determine day type from available context')


def r09_ib_tails(session_prof: dict) -> RuleResult:
    """
    R09 · IB Tails (Excess)
    Single prints at IB extremes = swift rejection = excess.
    Dalton: every completed session should have tails. No tail = auction incomplete.
    """
    ib_high  = session_prof.get('ib_high')
    ib_low   = session_prof.get('ib_low')
    profile  = session_prof.get('profile', [])   # [{price, letters, count}]

    if ib_high is None or ib_low is None or not profile:
        return _no_data('R09', 'IB Tails')

    # Find TPO rows at IB extremes (only A and B letters count for IB tails)
    ib_letters = {'A', 'B'}
    high_row = next((r for r in profile if r['price'] == ib_high), None)
    low_row  = next((r for r in profile if r['price'] == ib_low),  None)

    def _is_tail(row) -> bool:
        if row is None:
            return False
        # Single print = only one letter printed at this price, and it's an IB letter
        letters_at_price = set(row.get('letters', ''))
        ib_letters_here  = letters_at_price & ib_letters
        return len(letters_at_price) == 1 and len(ib_letters_here) == 1

    sell_tail = _is_tail(high_row)
    buy_tail  = _is_tail(low_row)

    if sell_tail and buy_tail:
        return RuleResult('R09', 'IB Tails', 'BOTH_TAILS',
                          f'Buy tail at {ib_low:.2f} and sell tail at {ib_high:.2f} — balanced IB, both sides rejected')
    if buy_tail:
        return RuleResult('R09', 'IB Tails', 'BUY_TAIL_ONLY',
                          f'Buy tail at {ib_low:.2f} — buyers strongly rejected the low. No sell tail at high')
    if sell_tail:
        return RuleResult('R09', 'IB Tails', 'SELL_TAIL_ONLY',
                          f'Sell tail at {ib_high:.2f} — sellers strongly rejected the high. No buy tail at low')
    return RuleResult('R09', 'IB Tails', 'NO_TAILS',
                      f'No tails at either IB extreme — market accepting both sides, IB may be exceeded')


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — Post-IB Development  (fires each period C onwards)
# ─────────────────────────────────────────────────────────────────────────────

def r10_first_extension(session_prof: dict, tick: float) -> RuleResult:
    """
    R10 · First Extension (Primary Trade Trigger)
    Dalton's highest-probability trade: first period close outside IB.
    Only the FIRST close outside IB in each direction is the signal.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    pr      = session_prof.get('period_ranges', {})

    if ib_high is None or ib_low is None:
        return _no_data('R10', 'First Extension')

    post_ib = [(ltr, data) for ltr, data in pr.items()
               if ltr >= 'C' and data.get('close') is not None]

    bull_ext = next(((ltr, d['close']) for ltr, d in post_ib
                     if d['close'] > ib_high + tick), None)
    bear_ext = next(((ltr, d['close']) for ltr, d in post_ib
                     if d['close'] < ib_low - tick), None)

    if bull_ext and bear_ext:
        b_ltr, b_close = bull_ext
        s_ltr, s_close = bear_ext
        return RuleResult('R10', 'First Extension', 'BOTH_EXTENSIONS',
                          f'Bull extension {b_ltr} ({b_close:.2f} > IB high {ib_high:.2f}) '
                          f'AND bear extension {s_ltr} ({s_close:.2f} < IB low {ib_low:.2f}) — Neutral Day developing')
    if bull_ext:
        ltr, close = bull_ext
        return RuleResult('R10', 'First Extension', 'BULL_EXTENSION',
                          f'{ltr} period first close above IB high ({ib_high:.2f}) at {close:.2f} — LONG signal')
    if bear_ext:
        ltr, close = bear_ext
        return RuleResult('R10', 'First Extension', 'BEAR_EXTENSION',
                          f'{ltr} period first close below IB low ({ib_low:.2f}) at {close:.2f} — SHORT signal')

    return RuleResult('R10', 'First Extension', 'NO_EXTENSION_YET',
                      f'No period has closed outside IB ({ib_low:.2f}–{ib_high:.2f}) — waiting')


def r11_extension_validity(session_prof: dict, tick: float) -> RuleResult:
    """
    R11 · Extension Validity
    After first extension fires, each subsequent close either holds or invalidates.
    INVALIDATED = Dalton's hard exit rule — close back inside IB, no exceptions.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')
    pr      = session_prof.get('period_ranges', {})

    if ib_high is None or ib_low is None:
        return _no_data('R11', 'Extension Validity')

    post_ib_closes = [(ltr, d['close']) for ltr, d in pr.items()
                      if ltr >= 'C' and d.get('close') is not None]

    if not post_ib_closes:
        return RuleResult('R11', 'Extension Validity', 'NO_EXTENSION_YET',
                          'No post-IB closes yet', fired=False)

    st = _st(tick)

    # Determine extension direction from first extension
    first_bull = next((c for _, c in post_ib_closes if c > ib_high + tick), None)
    first_bear = next((c for _, c in post_ib_closes if c < ib_low  - tick), None)

    if not first_bull and not first_bear:
        return RuleResult('R11', 'Extension Validity', 'NO_EXTENSION_YET',
                          'No extension has fired yet')

    # Check most recent close
    last_ltr, last_close = post_ib_closes[-1]

    if first_bull and not first_bear:
        if last_close > ib_high + tick:
            return RuleResult('R11', 'Extension Validity', 'HOLDING',
                              f'{last_ltr} holding above IB high ({ib_high:.2f}) at {last_close:.2f} — extension valid')
        if abs(last_close - ib_high) <= st:
            return RuleResult('R11', 'Extension Validity', 'RETESTING_IB_LEVEL',
                              f'{last_ltr} retesting IB high ({ib_high:.2f}) as support at {last_close:.2f} — watch next close')
        return RuleResult('R11', 'Extension Validity', 'INVALIDATED',
                          f'{last_ltr} closed back inside IB at {last_close:.2f} — EXTENSION FAILED. Exit longs.')

    if first_bear and not first_bull:
        if last_close < ib_low - tick:
            return RuleResult('R11', 'Extension Validity', 'HOLDING',
                              f'{last_ltr} holding below IB low ({ib_low:.2f}) at {last_close:.2f} — extension valid')
        if abs(last_close - ib_low) <= st:
            return RuleResult('R11', 'Extension Validity', 'RETESTING_IB_LEVEL',
                              f'{last_ltr} retesting IB low ({ib_low:.2f}) as resistance at {last_close:.2f} — watch next close')
        return RuleResult('R11', 'Extension Validity', 'INVALIDATED',
                          f'{last_ltr} closed back inside IB at {last_close:.2f} — EXTENSION FAILED. Cover shorts.')

    return RuleResult('R11', 'Extension Validity', 'BOTH_EXTENSIONS',
                      'Extensions in both directions — Neutral Day, no single direction to validate')


def r12_one_time_framing(session_prof: dict) -> RuleResult:
    """
    R12 · One-Time-Framing (OTF)
    Consecutive period closes in one direction = trend day developing.
    OTF broken = trend day over.
    """
    closes = _ordered_closes(session_prof.get('period_ranges', {}))

    if len(closes) < 2:
        return _no_data('R12', 'One-Time-Framing', 'Need at least 2 period closes')

    # Count current streak
    streak = 1
    direction = None
    last_ltr, last_close = closes[-1]

    for i in range(len(closes) - 2, -1, -1):
        _, prev_close = closes[i]
        if last_close > prev_close:
            curr_dir = 'UP'
        elif last_close < prev_close:
            curr_dir = 'DOWN'
        else:
            break  # equal close breaks streak

        if direction is None:
            direction = curr_dir
        elif curr_dir != direction:
            break

        streak += 1
        last_close = prev_close

    # Check if this period broke a prior streak
    prior_streak_dir = None
    if len(closes) >= 3:
        _, c2 = closes[-2]
        _, c3 = closes[-3]
        if c2 > c3:
            prior_streak_dir = 'UP'
        elif c2 < c3:
            prior_streak_dir = 'DOWN'

    curr_ltr, curr_close = closes[-1]
    _, prev_close = closes[-2]

    if direction == 'UP' and streak >= OTF_TREND_THRESH:
        return RuleResult('R12', 'One-Time-Framing', f'OTF_UP_{streak}',
                          f'{streak} consecutive higher closes through {curr_ltr} — trend day developing bullish')
    if direction == 'DOWN' and streak >= OTF_TREND_THRESH:
        return RuleResult('R12', 'One-Time-Framing', f'OTF_DOWN_{streak}',
                          f'{streak} consecutive lower closes through {curr_ltr} — trend day developing bearish')
    if prior_streak_dir == 'UP' and curr_close < prev_close:
        return RuleResult('R12', 'One-Time-Framing', 'OTF_BROKEN_FROM_UP',
                          f'{curr_ltr} closed lower — OTF UP sequence broken. Trend day may be exhausting')
    if prior_streak_dir == 'DOWN' and curr_close > prev_close:
        return RuleResult('R12', 'One-Time-Framing', 'OTF_BROKEN_FROM_DOWN',
                          f'{curr_ltr} closed higher — OTF DOWN sequence broken. Trend day may be exhausting')

    return RuleResult('R12', 'One-Time-Framing', 'NO_OTF',
                      f'No clear consecutive sequence — rotational or early session')


def r13_value_migration(session_prof: dict, tick: float) -> RuleResult:
    """
    R13 · Value Migration
    3+ consecutive post-IB closes outside IB = new value being established.
    """
    ib_high = session_prof.get('ib_high')
    ib_low  = session_prof.get('ib_low')

    if ib_high is None or ib_low is None:
        return _no_data('R13', 'Value Migration')

    post_ib = [(ltr, d['close']) for ltr, d in
               session_prof.get('period_ranges', {}).items()
               if ltr >= 'C' and d.get('close') is not None]

    if len(post_ib) < MIGRATION_PERIODS:
        return RuleResult('R13', 'Value Migration', 'INSUFFICIENT_DATA',
                          f'Need {MIGRATION_PERIODS} post-IB closes — only {len(post_ib)} so far', fired=False)

    last_n = post_ib[-MIGRATION_PERIODS:]
    all_above = all(c > ib_high + tick for _, c in last_n)
    all_below = all(c < ib_low  - tick for _, c in last_n)

    if all_above:
        return RuleResult('R13', 'Value Migration', 'MIGRATING_UP',
                          f'Last {MIGRATION_PERIODS} closes all above IB high ({ib_high:.2f}) — value migrating higher, add longs')
    if all_below:
        return RuleResult('R13', 'Value Migration', 'MIGRATING_DOWN',
                          f'Last {MIGRATION_PERIODS} closes all below IB low ({ib_low:.2f}) — value migrating lower, add shorts')

    midpoint = (ib_high + ib_low) / 2
    alternating = any(abs(c - midpoint) < (ib_high - midpoint) * 0.3 for _, c in last_n)
    if alternating:
        return RuleResult('R13', 'Value Migration', 'ROTATING',
                          'Closes oscillating around IB midpoint — rotational day, no migration')

    return RuleResult('R13', 'Value Migration', 'MIXED',
                      'Mixed closes — no clear migration pattern yet')


def r14_on_poc_post_ib(session_prof: dict, overnight: dict,
                       tick: float) -> RuleResult:
    """
    R14 · ON POC Post-IB
    After IB, overnight POC transitions to key intraday support/resistance.
    """
    on_poc = overnight.get('poc')
    closes = _ordered_closes(session_prof.get('period_ranges', {}))
    post_ib_closes = [(ltr, c) for ltr, c in closes if ltr >= 'C']

    if on_poc is None or not post_ib_closes:
        return _no_data('R14', 'ON POC Post-IB')

    st = _st(tick) * 2   # slightly wider tolerance for post-IB
    last_ltr, last_close = post_ib_closes[-1]

    if abs(last_close - on_poc) > st * 3:
        return RuleResult('R14', 'ON POC Post-IB', 'NOT_IN_PLAY',
                          f'Price ({last_close:.2f}) not near ON POC ({on_poc:.2f})', fired=False)

    # Need at least 2 closes to determine approach direction
    if len(post_ib_closes) < 2:
        return RuleResult('R14', 'ON POC Post-IB', 'APPROACHING',
                          f'{last_ltr} approaching ON POC ({on_poc:.2f}) — watch next close')

    prev_ltr, prev_close = post_ib_closes[-2]
    approaching_from_above = prev_close > on_poc and last_close <= on_poc + st * 3
    approaching_from_below = prev_close < on_poc and last_close >= on_poc - st * 3

    if approaching_from_above:
        if last_close > on_poc + st:
            return RuleResult('R14', 'ON POC Post-IB', 'SUPPORT_HELD',
                              f'{last_ltr} tested ON POC ({on_poc:.2f}) from above and bounced to {last_close:.2f} — support confirmed')
        return RuleResult('R14', 'ON POC Post-IB', 'SUPPORT_BROKEN',
                          f'{last_ltr} closed below ON POC ({on_poc:.2f}) at {last_close:.2f} — structural support lost')

    if approaching_from_below:
        if last_close < on_poc - st:
            return RuleResult('R14', 'ON POC Post-IB', 'RESISTANCE_HELD',
                              f'{last_ltr} tested ON POC ({on_poc:.2f}) from below and rejected to {last_close:.2f} — resistance confirmed')
        return RuleResult('R14', 'ON POC Post-IB', 'RESISTANCE_BROKEN',
                          f'{last_ltr} closed above ON POC ({on_poc:.2f}) at {last_close:.2f} — resistance broken, shorts covering')

    return RuleResult('R14', 'ON POC Post-IB', 'AT_POC',
                      f'{last_ltr} at ON POC ({on_poc:.2f}) — indecisive')


def r15_excess_at_extremes(session_prof: dict) -> RuleResult:
    """
    R15 · Excess at Session Extremes
    Single prints at session high/low signal strong rejection.
    Dalton: every completed session should have tails — missing tail = auction unfinished.
    """
    profile      = session_prof.get('profile', [])
    session_high = session_prof.get('session_high')
    session_low  = session_prof.get('session_low')

    if not profile or session_high is None or session_low is None:
        return _no_data('R15', 'Excess at Extremes')

    high_row = next((r for r in profile if r['price'] == session_high), None)
    low_row  = next((r for r in profile if r['price'] == session_low),  None)

    sell_tail = high_row is not None and high_row['count'] == 1
    buy_tail  = low_row  is not None and low_row['count']  == 1

    if sell_tail and buy_tail:
        return RuleResult('R15', 'Excess at Extremes', 'BOTH_TAILS_COMPLETE',
                          f'Buy tail at {session_low:.2f} and sell tail at {session_high:.2f} — auction nearing completion')
    if sell_tail:
        return RuleResult('R15', 'Excess at Extremes', 'SELL_TAIL_AT_HIGH',
                          f'Single print at session high ({session_high:.2f}) — sellers rejected that price, high likely done')
    if buy_tail:
        return RuleResult('R15', 'Excess at Extremes', 'BUY_TAIL_AT_LOW',
                          f'Single print at session low ({session_low:.2f}) — buyers rejected that price, low likely done')

    no_tail_notes = []
    if high_row and high_row['count'] > 1:
        no_tail_notes.append(f'no sell tail at high ({session_high:.2f}, {high_row["count"]} prints)')
    if low_row and low_row['count'] > 1:
        no_tail_notes.append(f'no buy tail at low ({session_low:.2f}, {low_row["count"]} prints)')

    return RuleResult('R15', 'Excess at Extremes', 'NO_TAILS',
                      'No tails at session extremes — ' + '; '.join(no_tail_notes) + ' — auction may continue beyond current range')


def r16_late_session(session_prof: dict) -> RuleResult:
    """
    R16 · Late Session Behavior  (J, K, L periods — 14:00–16:00 ET)
    Confirms or denies the day's narrative into settlement.
    """
    pr = session_prof.get('period_ranges', {})
    late_letters = [ltr for ltr in ('J', 'K', 'L') if pr.get(ltr, {}).get('close')]

    if not late_letters:
        return RuleResult('R16', 'Late Session', 'NOT_YET',
                          'Late session periods (J/K/L) not yet printed', fired=False)

    session_high = session_prof.get('session_high')
    session_low  = session_prof.get('session_low')
    last_ltr     = late_letters[-1]
    last_close   = pr[last_ltr]['close']

    if session_high is None or session_low is None:
        return _no_data('R16', 'Late Session')

    session_range = session_high - session_low
    if session_range <= 0:
        return _no_data('R16', 'Late Session', 'Session range is zero')

    midpoint   = (session_high + session_low) / 2
    near_high  = session_high - session_range * NEAR_EXT_PCT * 5
    near_low   = session_low  + session_range * NEAR_EXT_PCT * 5
    near_mid   = session_range * 0.10

    # Check for new late-session extreme
    for ltr in late_letters:
        p = pr[ltr]
        if p['high'] >= session_high:
            return RuleResult('R16', 'Late Session', 'LATE_NEW_HIGH',
                              f'{ltr} printed new session high ({session_high:.2f}) — Normal Variation Day confirmed bullish')
        if p['low'] <= session_low:
            return RuleResult('R16', 'Late Session', 'LATE_NEW_LOW',
                              f'{ltr} printed new session low ({session_low:.2f}) — Normal Variation Day confirmed bearish')

    if last_close >= near_high:
        return RuleResult('R16', 'Late Session', 'STRONG_CLOSE',
                          f'{last_ltr} closing strong at {last_close:.2f} near session high — buyers in control into settlement')
    if last_close <= near_low:
        return RuleResult('R16', 'Late Session', 'WEAK_CLOSE',
                          f'{last_ltr} closing weak at {last_close:.2f} near session low — sellers in control into settlement')
    if abs(last_close - midpoint) <= near_mid:
        return RuleResult('R16', 'Late Session', 'BALANCED_CLOSE',
                          f'{last_ltr} closing near midpoint ({midpoint:.2f}) at {last_close:.2f} — neutral settlement')

    return RuleResult('R16', 'Late Session', 'MID_RANGE_CLOSE',
                      f'{last_ltr} closing at {last_close:.2f} — mid-range, no strong directional close')


# ─────────────────────────────────────────────────────────────────────────────
# Phase Runners
# ─────────────────────────────────────────────────────────────────────────────

def run_phase1(session_prof: dict, overnight: dict,
               prior_rth: dict, tick: float) -> PhaseResult:
    """Phase 1 — Opening Context. Requires A period closed."""
    pr = session_prof.get('period_ranges', {})
    if 'A' not in pr or pr['A'].get('close') is None:
        return PhaseResult(phase=1, ready=False,
                           message='Waiting for A period to close (10:00 ET)')

    r1 = r01_open_type(session_prof, overnight, prior_rth, tick)
    r2 = r02_open_vs_prior_va(session_prof, prior_rth)
    r3 = r03_open_vs_on_poc(session_prof, overnight, tick)

    return PhaseResult(phase=1, ready=True, results=[r1, r2, r3])


def run_phase2(session_prof: dict, overnight: dict, prior_rth: dict,
               tick: float, now_et) -> PhaseResult:
    """Phase 2 — Initial Balance. Hard gate: 10:30 AM ET."""
    t_min = now_et.hour * 60 + now_et.minute
    if t_min < 10 * 60 + 30:
        remaining = (10 * 60 + 30) - t_min
        return PhaseResult(phase=2, ready=False,
                           message=f'IB not complete — {remaining} min until B period closes at 10:30 ET')

    p1 = run_phase1(session_prof, overnight, prior_rth, tick)
    r1 = next((r for r in p1.results if r.rule_id == 'R01'),
              RuleResult('R01', 'Open Type', 'NO_DATA', '', fired=False))

    r4 = r04_ib_width(session_prof, prior_rth)
    r5 = r05_ib_vs_on_range(session_prof, overnight, tick)
    r6 = r06_ib_vs_prior_va(session_prof, prior_rth)
    r7 = r07_on_poc_during_ib(session_prof, overnight, tick)
    r8 = r08_day_type_estimate(r4, r5, r1)
    r9 = r09_ib_tails(session_prof)

    return PhaseResult(phase=2, ready=True, results=[r4, r5, r6, r7, r8, r9])


def run_phase3(session_prof: dict, overnight: dict,
               tick: float) -> PhaseResult:
    """Phase 3 — Post-IB Development. Requires Phase 2 complete."""
    pr = session_prof.get('period_ranges', {})
    post_ib = [ltr for ltr in pr if ltr >= 'C' and pr[ltr].get('close') is not None]

    if not post_ib:
        return PhaseResult(phase=3, ready=False,
                           message='Waiting for C period close (11:00 ET)')

    r10 = r10_first_extension(session_prof, tick)
    r11 = r11_extension_validity(session_prof, tick)
    r12 = r12_one_time_framing(session_prof)
    r13 = r13_value_migration(session_prof, tick)
    r14 = r14_on_poc_post_ib(session_prof, overnight, tick)
    r15 = r15_excess_at_extremes(session_prof)
    r16 = r16_late_session(session_prof)

    return PhaseResult(phase=3, ready=True,
                       results=[r10, r11, r12, r13, r14, r15, r16])


def run_all(session_prof: dict, overnight: dict, prior_rth: dict,
            tick: float, now_et) -> dict:
    """
    Run all three phases and return a structured result.
    This is the main entry point for the API and backtest runner.
    """
    p1 = run_phase1(session_prof, overnight, prior_rth, tick)
    p2 = run_phase2(session_prof, overnight, prior_rth, tick, now_et)
    p3 = run_phase3(session_prof, overnight, tick)

    return {
        'phase1': {
            'ready':   p1.ready,
            'message': p1.message,
            'rules':   [_result_to_dict(r) for r in p1.results],
        },
        'phase2': {
            'ready':   p2.ready,
            'message': p2.message,
            'rules':   [_result_to_dict(r) for r in p2.results],
        },
        'phase3': {
            'ready':   p3.ready,
            'message': p3.message,
            'rules':   [_result_to_dict(r) for r in p3.results],
        },
    }


def _result_to_dict(r: RuleResult) -> dict:
    return {
        'rule_id': r.rule_id,
        'name':    r.name,
        'state':   r.state,
        'detail':  r.detail,
        'fired':   r.fired,
    }
