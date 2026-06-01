"""
vbh_updater.py
--------------
Core VBH table update logic — usable from the FastAPI background loop
or from the CLI (update_vbh_tables.py).

Public API
----------
run_update(tickers=None) -> dict
    Fetch new 30-min Schwab candles for active symbols, persist the raw
    30-min bars to ohlc_30min (new), aggregate to hourly and persist to
    ohlc_hourly, then compute VBH stats from the 30-min source (matching
    the original TOS study timeframe) and upsert to vbh_stats.

    tickers  — optional list of tickers to restrict to (default: all active)
    returns  — {'ok': ['/ES', ...], 'failed': ['/NQ', ...]}

Data flow:
    Schwab 30-min API
        ↓  raw bars
    ohlc_30min  ← NEW — permanent 30-min store, grows incrementally
        ↓  aggregate (max H, min L per hour bucket)
    ohlc_hourly ← kept for backward compatibility / dashboard display
        ↓  per-hour μ/σ from 30-min H-L ranges (not hourly H-L)
    vbh_stats   ← L1/L2/L3/L4 for AGG / CON / WIDE
"""

import logging, time, requests
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

LOOKBACK_DAYS    = 365   # raised from 90 — more history → stable σ, especially H9
LOOKBACK_DAYS_30 = 365   # same window for 30-min source
ET               = ZoneInfo('America/New_York')

# 2022 original methodology (reverse-engineered from Dec-2022 TOS study):
#
#   μ  = mean of hourly (H-L) ranges over lookback period
#   σ  = sample std dev of hourly (H-L) ranges over lookback period
#
#   AGG: L1 = μ - σ        L2 = μ          L3 = μ + σ        L4 ≈ L1
#   CON: L1 = μ + 1.4σ     L2 = μ + 2.4σ   L3 = μ + 3.4σ     L4 ≈ L1
#
#   CON is a 1σ-wide band identical to AGG but shifted up by 2.4σ — representing
#   a more volatile expected range. Both studies share the same σ.
#   L4 (T2 target) ≈ L1 in both studies (confirmed from 2022 data; exact formula unknown).
CON_SHIFT  = 2.4   # CON  centre = μ + CON_SHIFT  × σ
WIDE_SHIFT = 4.0   # WIDE centre = μ + WIDE_SHIFT × σ  (extra conservative)

PRICE_HISTORY_URL = 'https://api.schwabapi.com/marketdata/v1/pricehistory'


# ── Schwab fetch (uses schwab_client token — no local file dependency) ────────

def _fetch_candles(api_symbol: str, start_ms: int) -> list[dict]:
    """Fetch 30-min candles for api_symbol from start_ms to now.

    Uses schwab_client's token management (auto-refreshes, no token.json).
    Strips exchange suffix from symbol (e.g. /ES:XCME → /ES).
    Returns [] on any error.
    """
    from schwab_client import _headers  # lazy import — not available in tests

    # Strip exchange suffix (/ES:XCME → /ES) — pricehistory rejects qualified form
    clean = api_symbol.split(':')[0]
    end_ms = int(time.time() * 1000)

    try:
        resp = requests.get(
            PRICE_HISTORY_URL,
            headers=_headers(),
            params={
                'symbol'               : clean,
                'frequencyType'        : 'minute',
                'frequency'            : 30,
                'startDate'            : start_ms,
                'endDate'              : end_ms,
                'needExtendedHoursData': True,
            },
            timeout=20,
        )
        if resp.status_code == 401:
            from schwab_client import _token_cache
            _token_cache['expires_at'] = 0          # force token refresh
            resp = requests.get(
                PRICE_HISTORY_URL, headers=_headers(),
                params=resp.request.url.split('?')[1] if '?' in resp.request.url else {},
                timeout=20,
            )
        data = resp.json()
    except Exception as e:
        log.warning('VBH fetch error for %s: %s', clean, e)
        return []

    return data.get('candles', [])


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _candles_to_30min_rows(symbol_id: int, candles: list[dict]) -> list[dict]:
    """Convert raw Schwab 30-min candles to ohlc_30min rows.

    Schwab returns candles already at 30-min resolution; we just
    normalise and tag each bar with hour_et / minute_et.
    """
    rows = []
    for c in candles:
        dt_utc = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
        dt_et  = dt_utc.astimezone(ET)
        # Normalise bar_time to exact :00 or :30 boundary (drop seconds)
        bar_utc = dt_utc.replace(second=0, microsecond=0)
        rows.append({
            'symbol_id': symbol_id,
            'bar_time' : bar_utc.isoformat(),
            'hour_et'  : dt_et.hour,
            'minute_et': 0 if dt_et.minute < 30 else 30,
            'open'     : float(c['open']),
            'high'     : float(c['high']),
            'low'      : float(c['low']),
            'close'    : float(c['close']),
            'volume'   : int(c.get('volume') or 0),
        })
    return rows


def _aggregate_to_hourly(symbol_id: int, candles: list[dict]) -> list[dict]:
    """Aggregate 30-min candles into hourly OHLCV rows for ohlc_hourly upsert."""
    buckets: dict[datetime, dict] = {}

    for c in candles:
        dt_utc     = datetime.fromtimestamp(c['datetime'] / 1000, tz=timezone.utc)
        bucket_utc = dt_utc.replace(minute=0, second=0, microsecond=0)

        if bucket_utc not in buckets:
            buckets[bucket_utc] = {
                'open'  : c['open'],
                'high'  : c['high'],
                'low'   : c['low'],
                'close' : c['close'],
                'volume': c.get('volume') or 0,
            }
        else:
            b = buckets[bucket_utc]
            if c['high'] > b['high']: b['high']   = c['high']
            if c['low']  < b['low']:  b['low']    = c['low']
            b['close']   = c['close']
            b['volume'] += (c.get('volume') or 0)

    rows = []
    for bucket_utc, b in sorted(buckets.items()):
        bucket_et = bucket_utc.astimezone(ET)
        rows.append({
            'symbol_id': symbol_id,
            'bar_time' : bucket_utc.isoformat(),
            'hour_et'  : bucket_et.hour,
            'open'     : float(b['open']),
            'high'     : float(b['high']),
            'low'      : float(b['low']),
            'close'    : float(b['close']),
            'volume'   : int(b['volume']),
        })
    return rows


def _compute_vbh_rows(symbol_id: int, bars_30min: list[dict]) -> list[dict]:
    """Compute AGG / CON / WIDE vbh_stats rows from 30-min bar data.

    Aggregates the two 30-min bars per hour into one hourly H-L range
    (max high, min low across :00 and :30 bars) — matching the original
    TOS study which plotted on hourly boxes using hourly H and L.

    Each (date, hour_et) bucket → one hourly H-L range observation.
    Across the lookback period, we collect one range per trading hour per day.

    Formula (unchanged from 2022 TOS study reconstruction):
      μ         = mean of hourly (H-L) ranges
      σ         = sample std dev of those ranges
      σ_eff     = min(σ, μ × 0.1473)   ← 2022 fixed k-ratio cap

      AGG : L1=μ−σ_eff,       L2=μ,         L3=μ+σ_eff,       L4=L1−0.385·σ_eff
      CON : L1=μ+1.4·σ_eff,   L2=μ+2.4·σ,  L3=μ+3.4·σ_eff,   L4=L1−0.385·σ_eff
      WIDE: L1=μ+3.0·σ_eff,   L2=μ+4.0·σ,  L3=μ+5.0·σ_eff,   L4=L1−0.385·σ_eff
    """
    # ── Step 1: aggregate 30-min bars → one hourly H-L per (date, hour) ───────
    from datetime import date as date_type
    hourly_buckets: dict[tuple[date_type, int], dict] = {}
    for row in bars_30min:
        bar_dt = datetime.fromisoformat(row['bar_time'])
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=timezone.utc)
        bar_et  = bar_dt.astimezone(ET)
        key     = (bar_et.date(), row['hour_et'])
        h, l    = float(row['high']), float(row['low'])
        if key not in hourly_buckets:
            hourly_buckets[key] = {'high': h, 'low': l}
        else:
            if h > hourly_buckets[key]['high']: hourly_buckets[key]['high'] = h
            if l < hourly_buckets[key]['low']:  hourly_buckets[key]['low']  = l

    # ── Step 2: collect H-L ranges per ET hour ────────────────────────────────
    hour_ranges: dict[int, list[float]] = defaultdict(list)
    for (_, h_et), b in hourly_buckets.items():
        r = b['high'] - b['low']
        if r > 0:
            hour_ranges[h_et].append(r)

    rows    = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for h in range(24):
        rs  = hour_ranges.get(h, [])
        obs = len(rs)

        if obs >= 3:
            mu  = sum(rs) / obs
            var = sum((x - mu) ** 2 for x in rs) / (obs - 1)   # sample variance
            sigma = var ** 0.5

            # Cap σ at 14.73% of μ — matches 2022 study fixed k-ratio character.
            # _AGG_2022 shows σ/μ = 14.730% ±0.003% across all 240 non-zero RTH pairs,
            # confirming the 2022 study used a fixed k-ratio rather than raw σ.
            sigma_eff = min(sigma, mu * 0.1473)

            # AGG — band centred at μ, width = σ_eff
            a_l2 = mu
            a_l1 = max(a_l2 - sigma_eff, 0.0)
            a_l3 = a_l2 + sigma_eff
            a_l4 = max(a_l1 - sigma_eff * 0.385, 0.0)  # T2: 0.385·σ_eff outside entry

            # CON — same ±σ_eff band shifted up by 2.4 × σ_eff
            c_l2 = mu + CON_SHIFT * sigma_eff
            c_l1 = c_l2 - sigma_eff              # = μ + 1.4σ_eff
            c_l3 = c_l2 + sigma_eff              # = μ + 3.4σ_eff
            c_l4 = max(c_l1 - sigma_eff * 0.385, 0.0)  # T2: 0.385·σ_eff outside entry

            # WIDE — same ±σ_eff band shifted up by 4.0 × σ_eff (extra conservative)
            w_l2 = mu + WIDE_SHIFT * sigma_eff
            w_l1 = w_l2 - sigma_eff              # = μ + 3.0σ_eff
            w_l3 = w_l2 + sigma_eff              # = μ + 5.0σ_eff
            w_l4 = max(w_l1 - sigma_eff * 0.385, 0.0)

            r5 = lambda v: round(v, 5)
            l1,  l2,  l3,  l4  = r5(a_l1),  r5(a_l2),  r5(a_l3),  r5(a_l4)
            cl1, cl2, cl3, cl4 = r5(c_l1),  r5(c_l2),  r5(c_l3),  r5(c_l4)
            wl1, wl2, wl3, wl4 = r5(w_l1),  r5(w_l2),  r5(w_l3),  r5(w_l4)
        else:
            l1  = l2  = l3  = l4  = 0.0
            cl1 = cl2 = cl3 = cl4 = 0.0
            wl1 = wl2 = wl3 = wl4 = 0.0

        rows.append({'symbol_id': symbol_id, 'model': 'AGG', 'hour_et': h,
                     'l1': l1, 'l2': l2, 'l3': l3, 'l4': l4,
                     'sample_count': obs, 'lookback_days': LOOKBACK_DAYS,
                     'computed_at': now_iso})

        rows.append({'symbol_id': symbol_id, 'model': 'CON', 'hour_et': h,
                     'l1': cl1, 'l2': cl2, 'l3': cl3, 'l4': cl4,
                     'sample_count': obs, 'lookback_days': LOOKBACK_DAYS,
                     'computed_at': now_iso})

        rows.append({'symbol_id': symbol_id, 'model': 'WIDE', 'hour_et': h,
                     'l1': wl1, 'l2': wl2, 'l3': wl3, 'l4': wl4,
                     'sample_count': obs, 'lookback_days': LOOKBACK_DAYS,
                     'computed_at': now_iso})

    return rows


# ── Main update function ───────────────────────────────────────────────────────

def run_update(tickers: list[str] | None = None,
               include_stocks: bool = False,
               vbh_for_stocks: bool = False) -> dict:
    """Fetch new 30-min bars for all targets, compute VBH stats selectively.

    tickers        — optional list of tickers to restrict to (default: all active).
                     Futures: '/ES', '/NQ', …   Stocks: 'SPY', 'QQQ', …
    include_stocks — when True, fetches 30-min bars for stocks/ETFs too
                     (default: False → futures only).
    vbh_for_stocks — when True AND include_stocks=True, also recomputes VBH stats
                     for stocks/ETFs (default: False → stocks get bar storage only,
                     VBH recompute skipped until weekend run).

    Scheduling intent:
      Daily 5:30 AM  : run_update(include_stocks=True)
                         → new bars for everyone, VBH recomputed for futures only
      Saturday 8 AM  : run_update(include_stocks=True, vbh_for_stocks=True)
                         → VBH recomputed for all from full week's bars

    Returns {'ok': [...], 'failed': [...]}
    """
    from db import (get_db, upsert_ohlc, upsert_vbh_stats,
                    upsert_30min, get_30min, get_last_30min_bar_times)
    import vbh_engine

    # ── Determine target symbols ───────────────────────────────────────────────
    all_syms = (get_db().table('symbols')
                .select('id,ticker,schwab_symbol,asset_type')
                .eq('is_active', True).order('id').execute().data)

    if include_stocks:
        targets = list(all_syms)                          # futures + stocks
    else:
        targets = [s for s in all_syms if s['ticker'].startswith('/')]  # futures only

    if tickers:
        targets = [s for s in targets if s['ticker'] in tickers
                   or s['ticker'].lstrip('/') in tickers]

    if not targets:
        log.warning('VBH update: no active symbols found')
        return {'ok': [], 'failed': []}

    log.info('VBH update starting — %d symbol(s), lookback=%dd, vbh_for_stocks=%s',
             len(targets), LOOKBACK_DAYS, vbh_for_stocks)

    # ── Incremental start: use ohlc_30min last bar as watermark ───────────────
    symbol_ids     = [s['id'] for s in targets]
    last_bar_times = get_last_30min_bar_times(symbol_ids)
    default_start  = int((time.time() - LOOKBACK_DAYS * 86400) * 1000)

    ok, failed = [], []

    for sym in targets:
        sid       = sym['id']
        ticker    = sym['ticker']
        schwab    = sym['schwab_symbol']
        is_future = ticker.startswith('/')
        do_vbh    = is_future or vbh_for_stocks   # futures always; stocks only on weekend

        # Determine incremental start from ohlc_30min watermark
        last_bt = last_bar_times.get(sid)
        if last_bt:
            last_dt = datetime.fromisoformat(last_bt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            start_ms = int(last_dt.timestamp() * 1000) + 1
        else:
            start_ms = default_start

        # ── Fetch 30-min candles from Schwab ──────────────────────────────
        candles    = _fetch_candles(schwab, start_ms)
        rows_30min = []
        hourly_rows = []

        if candles:
            # ── Persist raw 30-min bars ────────────────────────────────────
            rows_30min = _candles_to_30min_rows(sid, candles)
            if rows_30min:
                upsert_30min(rows_30min)

            # ── Also aggregate → ohlc_hourly (backward compat / dashboard) ─
            hourly_rows = _aggregate_to_hourly(sid, candles)
            if hourly_rows:
                upsert_ohlc(hourly_rows)

        elif do_vbh:
            # No new bars from Schwab — but we still need to (re)compute VBH
            # from existing DB history (e.g. weekend recompute after 5:30 AM
            # already stored the latest bars).
            log.debug('VBH %s: no new candles — recomputing VBH from DB history', ticker)

        else:
            # Bar-fetch only mode and nothing new — not an error, just skip
            log.debug('VBH bars: no new 30-min bars for %s (already current)', ticker)
            time.sleep(0.4)
            continue

        # ── VBH stats (futures always; stocks only when vbh_for_stocks=True) ─
        if do_vbh:
            bars = get_30min(sid, LOOKBACK_DAYS_30)
            if not bars:
                log.warning('VBH update: no 30-min bars in DB for %s', ticker)
                failed.append(ticker)
                time.sleep(0.4)
                continue

            stat_rows = _compute_vbh_rows(sid, bars)
            upsert_vbh_stats(stat_rows)

            rth = [r['sample_count'] for r in stat_rows
                   if r['model'] == 'AGG' and 9 <= r['hour_et'] < 17]
            log.info('VBH %s: %d new 30m bars → %d hourly, %d–%d obs/RTH hour',
                     ticker, len(rows_30min), len(hourly_rows),
                     min(rth) if rth else 0, max(rth) if rth else 0)
        else:
            log.info('VBH %s: %d new 30m bars stored (VBH recompute scheduled for weekend)',
                     ticker, len(rows_30min))

        ok.append(ticker)
        time.sleep(0.4)   # rate-limit between symbols

    # ── Reload engine cache so live signals pick up new levels immediately ─────
    if ok:
        try:
            vbh_engine.load_stats_from_db()
            log.info('VBH cache reloaded — %d tickers ready', len(vbh_engine._stats_db))
        except Exception as e:
            log.warning('VBH cache reload error: %s', e)

    log.info('VBH update done — ok=%s  failed=%s', ok, failed)
    return {'ok': ok, 'failed': failed}
