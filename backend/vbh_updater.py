"""
vbh_updater.py
--------------
Core VBH table update logic — usable from the FastAPI background loop
or from the CLI (update_vbh_tables.py).

Public API
----------
run_update(tickers=None) -> dict
    Fetch new 30-min Schwab candles for active futures, aggregate to
    hourly H/L buckets, persist to ohlc_hourly, recompute ATR means,
    apply confirmed VBH k-ratios, upsert to vbh_stats, then reload the
    vbh_engine in-memory cache.

    tickers  — optional list of /XX tickers to restrict to (default: all active)
    returns  — {'ok': ['/ES', ...], 'failed': ['/NQ', ...]}
"""

import logging, time, requests
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

LOOKBACK_DAYS = 90
ET            = ZoneInfo('America/New_York')

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


def _compute_vbh_rows(symbol_id: int, ohlc_rows: list[dict]) -> list[dict]:
    """Compute AGG + CON vbh_stats rows from 90-day ohlc_hourly data.

    Replicates the 2022 TOS study methodology:
      μ         = mean of hourly (H-L) ranges
      σ         = sample std dev of hourly (H-L) ranges
      σ_eff     = min(σ, μ × 0.1473)   ← cap matches 2022 fixed k-ratio

    WHY 14.73%:
      Analysis of _AGG_2022 (the authoritative 2022 reference hardcoded in
      vbh_engine.py) shows σ/μ = 14.730% ±0.003% across ALL 240 non-zero RTH
      hour/symbol pairs — impossibly uniform for raw σ.  Conclusion: the 2022
      study applied a fixed k-ratio of 14.73% rather than the raw sample σ.
      Capping at 14.73% replicates this character: L1 ≥ 0.8527μ → inversions
      only when range > 1.705μ.  In 2026, raw σ/μ ~34%; the cap brings
      effective σ back to 2022 proportions.

    L4: T2 target sits 0.385·σ_eff OUTSIDE L1 (away from centre).
      (_AGG_2022: (L1−L4)/σ_eff = 0.3849 ±0.003, consistent across all 240 pairs.)

      AGG: L1=μ-σ_eff,         L2=μ,            L3=μ+σ_eff,       L4=L1-0.385·σ_eff
      CON: L1=μ+1.4σ_eff,      L2=μ+2.4σ_eff,   L3=μ+3.4σ_eff,    L4=L1-0.385·σ_eff
    """
    hour_ranges: dict[int, list[float]] = defaultdict(list)
    for row in ohlc_rows:
        r = row['high'] - row['low']
        if r > 0:
            hour_ranges[row['hour_et']].append(r)

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
               include_stocks: bool = False) -> dict:
    """Fetch new candles, update ohlc_hourly + vbh_stats, reload vbh_engine cache.

    tickers        — optional list of tickers to restrict to (default: all active).
                     Futures: '/ES', '/NQ', …   Stocks: 'SPY', 'QQQ', …
    include_stocks — when True, also updates stocks/ETFs (default: False, futures only).
                     Stocks use the same 90-day lookback and σ_eff formula as futures.

    Returns {'ok': [...], 'failed': [...]}
    """
    from db import (get_db, upsert_ohlc, get_ohlc, upsert_vbh_stats,
                    get_last_ohlc_bar_times)
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
        # Accept both '/ES' and 'SPY' style — match against ticker column
        targets = [s for s in targets if s['ticker'] in tickers
                   or s['ticker'].lstrip('/') in tickers]

    if not targets:
        log.warning('VBH update: no active symbols found')
        return {'ok': [], 'failed': []}

    log.info('VBH update starting — %d symbol(s)', len(targets))

    # ── Incremental start times ────────────────────────────────────────────────
    symbol_ids      = [s['id'] for s in targets]
    last_bar_times  = get_last_ohlc_bar_times(symbol_ids)
    default_start   = int((time.time() - LOOKBACK_DAYS * 86400) * 1000)

    ok, failed = [], []

    for sym in targets:
        sid    = sym['id']
        ticker = sym['ticker']
        schwab = sym['schwab_symbol']

        # Determine incremental start
        last_bt = last_bar_times.get(sid)
        if last_bt:
            last_dt = datetime.fromisoformat(last_bt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            start_ms = int(last_dt.timestamp() * 1000) + 1
        else:
            start_ms = default_start

        # Fetch
        candles = _fetch_candles(schwab, start_ms)
        if not candles:
            log.warning('VBH update: no candles for %s — skipping', ticker)
            failed.append(ticker)
            time.sleep(0.4)
            continue

        # Aggregate → upsert ohlc_hourly
        hourly_rows = _aggregate_to_hourly(sid, candles)
        if not hourly_rows:
            log.warning('VBH update: no hourly rows after aggregation for %s', ticker)
            failed.append(ticker)
            time.sleep(0.4)
            continue

        upsert_ohlc(hourly_rows)

        # Load 90-day window → compute stats → upsert vbh_stats
        ohlc_rows = get_ohlc(sid, LOOKBACK_DAYS)
        if not ohlc_rows:
            log.warning('VBH update: no ohlc rows in DB for %s after upsert', ticker)
            failed.append(ticker)
            time.sleep(0.4)
            continue

        stat_rows = _compute_vbh_rows(sid, ohlc_rows)
        upsert_vbh_stats(stat_rows)

        rth = [r['sample_count'] for r in stat_rows
               if r['model'] == 'AGG' and 9 <= r['hour_et'] < 17]
        log.info('VBH %s: %d new candles → %d hourly bars, %d–%d obs/RTH hour',
                 ticker, len(candles), len(hourly_rows),
                 min(rth) if rth else 0, max(rth) if rth else 0)

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
