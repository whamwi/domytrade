"""
VBH Weekly Import — futures + stocks.

Usage:
    python3 vbh_import_weekly.py \
        --futures /Users/wassim/Downloads/Volatility-Box-Futures-Jun.-14-bd1eik \
        --stocks  /Users/wassim/Downloads/Volatility-Box-Stocks-2026-06-14

Both flags are optional — pass only what arrived this week.
After import, hot-reloads the live Railway backend automatically.
"""

import argparse
import os
import re
import httpx
from datetime import date
from supabase import create_client

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

def _week_of_from_path(path: str) -> date | None:
    """Extract week date from folder name.
    Handles: 'Volatility-Box-Futures-Jun.-21-xxx' and 'Volatility-Box-Stocks-2026-06-21'
    """
    name = os.path.basename(path.rstrip('/'))
    # Full ISO date: 2026-06-21
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', name)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # Abbreviated: Jun.-21
    m = re.search(r'([A-Za-z]{3})\.-?(\d+)', name)
    if m:
        month = MONTH_MAP.get(m.group(1).lower())
        day   = int(m.group(2))
        if month:
            return date(date.today().year, month, day)
    return None

# ── env ──────────────────────────────────────────────────────────────────────
env: dict[str, str] = {}
with open('/Users/wassim/domytrade/backend/.env') as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

sb = create_client(env['SUPABASE_URL'], env['SUPABASE_SERVICE_ROLE_KEY'])

# ── symbol lookup ─────────────────────────────────────────────────────────────
_syms = sb.table('symbols').select('id,ticker').execute().data
_sym_by_ticker = {s['ticker']: s['id'] for s in _syms}

# ── futures config ────────────────────────────────────────────────────────────
FUTURES_SYM_MAP = {
    '/ES:XCME':  ('/ES',  5),
    '/NQ:XCME':  ('/NQ',  6),
    '/RTY:XCME': ('/RTY', 7),
    '/YM:XCBT':  ('/YM',  8),
    '/CL:XNYM':  ('/CL',  9),
    '/NG:XNYM':  ('/NG', 10),
    '/GC:XCEC':  ('/GC', 11),
    '/SI:XCEC':  ('/SI', 12),
    '/HG:XCEC':  ('/HG', 13),
    '/ZB:XCBT':  ('/ZB', 14),
}

FUTURES_MODEL_FILES = {
    'AGG':  'TI_VBH_AggressiveSTUDY.ts',
    'CON':  'TI_VBH_ConservativeSTUDY.ts',
    'WIDE': 'TI_VBH_DoomsdayConsSTUDY.ts',
}

# ── stocks config ─────────────────────────────────────────────────────────────
STOCKS_MODEL_FILES = {
    'AGG': 'TI_VBH_Stocks_AggressiveSTUDY.ts',
    'CON': 'TI_VBH_Stocks_ConservativeSTUDY.ts',
}

BLOCK_RE = re.compile(
    r'(?:if|else if)\s*\(GetSymbolPart\(\)\s*==\s*"([^"]+)"[^{]*\{([^}]+)\}',
    re.DOTALL,
)


def _upsert_rows(rows: list[dict]) -> None:
    for i in range(0, len(rows), 50):
        sb.table('vbh_stats').upsert(
            rows[i:i + 50],
            on_conflict='symbol_id,model,hour_et',
        ).execute()


def _upsert_weekly_rows(rows: list[dict], week_of: date) -> None:
    """Insert into vbh_ts_weekly for the analysis/comparison table."""
    weekly = [
        {
            'week_of':   week_of.isoformat(),
            'symbol_id': r['symbol_id'],
            'model':     r['model'],
            'hour_et':   r['hour_et'],
            'l1':        r['l1'],
            'l2':        r['l2'],
            'l3':        r['l3'],
            'l4':        r.get('l4'),
        }
        for r in rows
    ]
    for i in range(0, len(weekly), 50):
        sb.table('vbh_ts_weekly').upsert(
            weekly[i:i + 50],
            on_conflict='week_of,symbol_id,model,hour_et',
        ).execute()


def import_futures(base_path: str) -> None:
    print(f"\n── Futures: {base_path}")
    week_of = _week_of_from_path(base_path)
    if week_of:
        print(f"  week_of: {week_of}")
    else:
        print("  WARNING: could not parse week date from folder name — skipping vbh_ts_weekly")
    for model, fname in FUTURES_MODEL_FILES.items():
        path = f"{base_path.rstrip('/')}/{fname}"
        try:
            content = open(path).read()
        except FileNotFoundError:
            print(f"  {model}: {fname} not found — skipping")
            continue

        blocks = BLOCK_RE.findall(content)
        rows: list[dict] = []
        for raw_sym, vals_str in blocks:
            if raw_sym not in FUTURES_SYM_MAP:
                continue
            _, sym_id = FUTURES_SYM_MAP[raw_sym]
            for h in range(24):
                r: dict[str, float] = {}
                for m in re.finditer(
                    r'VBH' + str(h) + r'_L(\d)\s*=\s*([\d.]+)', vals_str
                ):
                    r[f'L{m.group(1)}'] = float(m.group(2))
                if r and all(k in r for k in ['L1', 'L2', 'L3', 'L4']):
                    rows.append({
                        'symbol_id':    sym_id,
                        'model':        model,
                        'hour_et':      h,
                        'l1':           r['L1'],
                        'l2':           r['L2'],
                        'l3':           r['L3'],
                        'l4':           r['L4'],
                        'lookback_days': -1,
                    })

        _upsert_rows(rows)
        if week_of:
            _upsert_weekly_rows(rows, week_of)
        print(f"  {model}: {len(rows)} rows upserted (vbh_stats + vbh_ts_weekly)")


def import_stocks(base_path: str) -> None:
    print(f"\n── Stocks: {base_path}")
    week_of = _week_of_from_path(base_path)
    if week_of:
        print(f"  week_of: {week_of}")
    else:
        print("  WARNING: could not parse week date from folder name — skipping vbh_ts_weekly")
    for model, fname in STOCKS_MODEL_FILES.items():
        path = f"{base_path.rstrip('/')}/{fname}"
        try:
            content = open(path).read()
        except FileNotFoundError:
            print(f"  {model}: {fname} not found — skipping")
            continue

        blocks = BLOCK_RE.findall(content)
        rows: list[dict] = []
        missing: list[str] = []

        for raw_sym, vals_str in blocks:
            sym_id = _sym_by_ticker.get(raw_sym)
            if sym_id is None:
                missing.append(raw_sym)
                continue
            for h in range(9, 16):   # RTH hours only for stocks
                r: dict[str, float] = {}
                for m in re.finditer(
                    r'VBH' + str(h) + r'_L(\d)\s*=\s*([\d.]+)', vals_str
                ):
                    r[f'L{m.group(1)}'] = float(m.group(2))
                if r and all(k in r for k in ['L1', 'L2', 'L3']):
                    # L4 not in stock files — derive from formula
                    sigma_eff = r['L2'] - r['L1']
                    l4 = max(r['L1'] - sigma_eff * 0.385, 0.0)
                    rows.append({
                        'symbol_id':    sym_id,
                        'model':        model,
                        'hour_et':      h,
                        'l1':           r['L1'],
                        'l2':           r['L2'],
                        'l3':           r['L3'],
                        'l4':           round(l4, 5),
                        'lookback_days': -1,
                    })

        _upsert_rows(rows)
        if week_of:
            _upsert_weekly_rows(rows, week_of)
        print(f"  {model}: {len(rows)} rows upserted (vbh_stats + vbh_ts_weekly)")
        if missing:
            print(f"  {model}: {len(missing)} symbols not in DB — {missing[:10]}")


def hot_reload() -> None:
    print("\n── Hot-reloading Railway backend …")
    try:
        r = httpx.post(
            'https://domytrade-backend-production.up.railway.app/api/reload-db-stats',
            timeout=30,
        )
        print(f"  {r.json()}")
    except Exception as e:
        print(f"  Hot-reload failed: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='VBH weekly ThinkScript import')
    parser.add_argument('--futures', help='Path to futures week folder')
    parser.add_argument('--stocks',  help='Path to stocks week folder')
    args = parser.parse_args()

    if not args.futures and not args.stocks:
        parser.error('Provide at least --futures or --stocks (or both)')

    if args.futures:
        import_futures(args.futures)
    if args.stocks:
        import_stocks(args.stocks)

    hot_reload()
    print("\nDone.")
