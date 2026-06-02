#!/usr/bin/env python3
"""
trim_1min.py — keep ohlc_1min to last 12 hours per symbol.
Run via cron every 6 hours. Prevents the Supabase 1000-row default cap
from returning stale data when get_1min_range queries without a limit fix.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from db import get_db
from datetime import datetime, timezone, timedelta

db     = get_db()
cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
syms   = db.table('symbols').select('id,ticker').execute().data
total  = 0

for sym in syms:
    sid = sym['id']
    res = db.table('ohlc_1min').delete().eq('symbol_id', sid).lt('bar_time', cutoff).execute()
    deleted = len(res.data) if res.data else 0
    total += deleted

print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] trim_1min: deleted {total} rows older than {cutoff[:16]} UTC")
