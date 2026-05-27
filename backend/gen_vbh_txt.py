#!/usr/bin/env python3
"""
gen_vbh_txt.py
--------------
Regenerate /Users/wassim/VBH_AGG_2026.txt and /Users/wassim/VBH_CON_2026.txt
from the live vbh_stats table in Supabase (trading project).

TOS format — each symbol uses exchange-qualified GetSymbolPart() conditions:
  if (GetSymbolPart() == "/ES:XCME") or (GetSymbolPart() == "/MES:XCME") {data}
  else if (GetSymbolPart() == "/NG:XNYM") {data}
  ...
  else{VBH0_L1=Double.NaN;...}

Mini-contracts are paired inline with their canonical using `or` — no separate line.

Run from /Users/wassim/domytrade/backend/
"""

import sys
from dotenv import load_dotenv

load_dotenv()

from db import load_vbh_stats_from_db

# Exchange suffix for every future tracked in TOS
EXCHANGE_SUFFIX: dict[str, str] = {
    '/ES':  'XCME', '/NQ':  'XCME', '/RTY': 'XCME', '/YM':  'XCBT',
    '/CL':  'XNYM', '/NG':  'XNYM', '/GC':  'XCEC', '/SI':  'XCEC',
    '/HG':  'XCEC', '/ZB':  'XCBT', '/ZN':  'XCBT', '/ZC':  'XCBT',
    '/ZS':  'XCBT', '/PL':  'XNYM', '/RB':  'XNYM', '/BTC': 'XCME',
    # Minis share the same exchange as their canonical
    '/MES': 'XCME', '/MNQ': 'XCME', '/M2K': 'XCME', '/MYM': 'XCBT',
    '/MCL': 'XNYM', '/MGC': 'XCEC',
}

# Canonical → mini base (both share identical L values; emitted on same line)
CANONICAL_MINI_PAIRS: dict[str, str] = {
    '/ES': '/MES', '/NQ': '/MNQ', '/RTY': '/M2K',
    '/YM': '/MYM', '/CL': '/MCL', '/GC':  '/MGC',
}

# Preferred output order — only futures, canonical symbols
CANONICAL_ORDER = [
    '/ES', '/NQ', '/YM', '/RTY',
    '/CL', '/GC', '/SI', '/HG', '/NG', '/PL', '/RB',
    '/ZB', '/ZN', '/ZC', '/ZS',
    '/BTC',
]


def _sym_condition(base: str) -> str:
    """Return one GetSymbolPart() sub-condition for base (with exchange suffix)."""
    exchange = EXCHANGE_SUFFIX.get(base, '')
    qualified = f'{base}:{exchange}' if exchange else base
    return f'(GetSymbolPart() == "{qualified}")'


def format_line(canonical: str, mini: str | None, hours: list[tuple]) -> str:
    """Build one ThinkScript if-condition + data block for a symbol.

    canonical — base ticker, e.g. '/ES'
    mini      — paired mini base, e.g. '/MES', or None
    hours     — list of 24 (L1,L2,L3,L4) tuples, index = ET hour
    """
    fmt = lambda v: f'{v:.5f}'   # fixed-point — avoids scientific notation

    condition = _sym_condition(canonical)
    if mini:
        condition += f' or {_sym_condition(mini)}'

    parts = []
    for h, (l1, l2, l3, l4) in enumerate(hours):
        parts.append(
            f'VBH{h}_L1={fmt(l1)};'
            f'VBH{h}_L2={fmt(l2)};'
            f'VBH{h}_L3={fmt(l3)};'
            f'VBH{h}_L4={fmt(l4)};'
        )
    inner = ''.join(parts)
    return f'if {condition} {{{inner}}}'


def nan_else_block() -> str:
    """Trailing else{} block that sets all VBH variables to Double.NaN."""
    parts = []
    for h in range(24):
        parts.append(
            f'VBH{h}_L1=Double.NaN;'
            f'VBH{h}_L2=Double.NaN;'
            f'VBH{h}_L3=Double.NaN;'
            f'VBH{h}_L4=Double.NaN;'
        )
    return 'else{' + ''.join(parts) + '}'


def build_output(stats: dict[str, dict[str, list[tuple]]], model: str) -> list[str]:
    """
    Build if/else if/else ThinkScript chain for the given model ('AGG' or 'CON').

    Only futures with entries in EXCHANGE_SUFFIX are included.
    Mini-contracts are paired inline with their canonical using `or`.
    Trailing else{Double.NaN} block closes the chain.
    """
    symbol_lines: list[str] = []
    emitted: set[str] = set()

    def emit(canonical: str, mini: str | None, hours: list[tuple]) -> None:
        symbol_lines.append(format_line(canonical, mini, hours))
        emitted.add(canonical)
        if mini:
            emitted.add(mini)

    # 1. Canonical futures in preferred order, minis paired inline
    for sym in CANONICAL_ORDER:
        if sym in stats and stats[sym].get(model):
            mini = CANONICAL_MINI_PAIRS.get(sym)   # None when no mini exists
            emit(sym, mini, stats[sym][model])

    # 2. Any futures not in CANONICAL_ORDER but present in DB and EXCHANGE_SUFFIX
    remaining = sorted(
        k for k in stats
        if k not in emitted
        and k in EXCHANGE_SUFFIX          # futures only — skip equities/ETFs
        and k not in CANONICAL_MINI_PAIRS.values()  # skip standalone minis
        and stats[k].get(model)
        and any(t[1] > 0 for t in stats[k][model])  # skip all-zero rows
    )
    for sym in remaining:
        emit(sym, None, stats[sym][model])

    # Chain: first line = "if ...", rest = "else if ..."
    lines: list[str] = []
    for i, line in enumerate(symbol_lines):
        lines.append(line if i == 0 else 'else ' + line)

    # Trailing else{Double.NaN}
    lines.append(nan_else_block())

    return lines


def main() -> None:
    print('Loading vbh_stats from Supabase…')
    stats = load_vbh_stats_from_db()

    if not stats:
        print('ERROR: no data returned from DB — aborting.')
        sys.exit(1)

    print(f'Loaded {len(stats)} symbols.')

    agg_lines = build_output(stats, 'AGG')
    con_lines = build_output(stats, 'CON')

    agg_path = '/Users/wassim/VBH_AGG_2026.txt'
    con_path = '/Users/wassim/VBH_CON_2026.txt'

    with open(agg_path, 'w') as f:
        f.write('\n'.join(agg_lines) + '\n')
    print(f'Wrote {len(agg_lines)} lines -> {agg_path}')

    with open(con_path, 'w') as f:
        f.write('\n'.join(con_lines) + '\n')
    print(f'Wrote {len(con_lines)} lines -> {con_path}')

    # Sanity check
    print('\nSanity check (RTH hours 9-16 L2 > 0):')
    for sym in ['/ES', '/NQ', '/CL', '/GC']:
        if sym in stats and stats[sym].get('AGG'):
            rth_ok = sum(1 for h in range(9, 17) if stats[sym]['AGG'][h][1] > 0)
            print(f'  {sym} AGG RTH hours with L2>0: {rth_ok}/8')

    # Preview first 3 lines of AGG to verify format
    print('\nAGG format preview (first 2 symbol lines, truncated):')
    for line in agg_lines[:2]:
        print(' ', line[:120], '…')


if __name__ == '__main__':
    main()
