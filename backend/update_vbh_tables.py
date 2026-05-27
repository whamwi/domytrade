#!/usr/bin/env python3
"""
update_vbh_tables.py
--------------------
CLI wrapper around vbh_updater.run_update().
Use this for manual runs or one-off backfills.
The app runs the same logic automatically at 5:30 AM ET via background_loop().

Usage:
    python3 update_vbh_tables.py                     # all active futures
    python3 update_vbh_tables.py /ES /NQ             # specific futures only
    python3 update_vbh_tables.py --stocks            # all futures + all stocks/ETFs
    python3 update_vbh_tables.py --stocks SPY QQQ    # specific stocks only
"""

import json, sys, time
from dotenv import load_dotenv
load_dotenv()

# ── Seed Schwab token from local token.json so schwab_client works without
#    SCHWAB_REFRESH_TOKEN env var (which is only available in Railway prod).
TOKEN_PATH = '/Users/wassim/token.json'
try:
    import schwab_client
    with open(TOKEN_PATH) as _f:
        _tok = json.load(_f)['token']
    schwab_client._token_cache['access_token'] = _tok['access_token']
    schwab_client._token_cache['expires_at']   = time.time() + 1800   # treat as fresh
    if _tok.get('refresh_token'):
        schwab_client._token_cache['refresh_token'] = _tok['refresh_token']
except Exception as _e:
    print(f'Warning: could not seed token from {TOKEN_PATH}: {_e}')

from vbh_updater import run_update

if __name__ == '__main__':
    args           = sys.argv[1:]
    include_stocks = '--stocks' in args
    tickers        = [a for a in args if a != '--stocks'] or None

    result = run_update(tickers=tickers, include_stocks=include_stocks)
    print(f'\nUpdated : {result["ok"]}')
    if result['failed']:
        print(f'Failed  : {result["failed"]}')
    print('\nDone — vbh_stats updated in Supabase.')
