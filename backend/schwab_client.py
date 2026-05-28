"""
Schwab API client — quotes and intraday candles.
Token management is self-contained: reads from env vars, refreshes in memory.
No dependency on local token.json or market_hours.py.
"""
import os, time, base64, requests, threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ── Futures contract month codes ───────────────────────────────────────────────
# Standard CME/CBOT month letter codes (all months)
MONTH_CODES = {
    1: 'F',   # January
    2: 'G',   # February
    3: 'H',   # March
    4: 'J',   # April
    5: 'K',   # May
    6: 'M',   # June
    7: 'N',   # July
    8: 'Q',   # August
    9: 'U',   # September
    10: 'V',  # October
    11: 'X',  # November
    12: 'Z',  # December
}

# Quarterly contract months for equity index futures (ES, NQ, YM, RTY)
# Rolls approximately on the 3rd Friday of the expiry month
QUARTERLY = [3, 6, 9, 12]   # H, M, U, Z

# Per-symbol expiry schedules for commodity futures.
# Key = root symbol (no leading slash, no exchange suffix).
# Value = sorted list of months that have listed contracts.
FUTURES_SCHEDULES: dict[str, list[int]] = {
    # ── Grains (CBOT) ──────────────────────────────────────────────────────────
    'ZC': [3, 5, 7, 9, 12],           # Corn: Mar/May/Jul/Sep/Dec
    'ZS': [1, 3, 5, 7, 8, 9, 11],     # Soybeans: Jan/Mar/May/Jul/Aug/Sep/Nov
    'ZW': [3, 5, 7, 9, 12],           # Wheat: Mar/May/Jul/Sep/Dec
    'ZM': [1, 3, 5, 7, 8, 9, 10, 12], # Soybean Meal
    'ZL': [1, 3, 5, 7, 8, 9, 10, 12], # Soybean Oil
    # ── Energy (NYMEX) ─────────────────────────────────────────────────────────
    'CL': list(range(1, 13)),          # Crude Oil: every month
    'NG': list(range(1, 13)),          # Natural Gas: every month
    'RB': list(range(1, 13)),          # RBOB Gasoline: every month
    'HO': list(range(1, 13)),          # Heating Oil: every month
    # ── Metals (COMEX) ─────────────────────────────────────────────────────────
    'GC': [2, 4, 6, 8, 10, 12],       # Gold: Feb/Apr/Jun/Aug/Oct/Dec
    'SI': [3, 5, 7, 9, 12],           # Silver: Mar/May/Jul/Sep/Dec
    'HG': [3, 5, 7, 9, 12],           # Copper: Mar/May/Jul/Sep/Dec
    'PL': [1, 4, 7, 10],              # Platinum
    # ── Softs / Other ──────────────────────────────────────────────────────────
    'KC': [3, 5, 7, 9, 12],           # Coffee
    'CT': [3, 5, 7, 10, 12],          # Cotton
    'SB': [3, 5, 7, 10],              # Sugar
}

# Symbols whose contract expires in the month BEFORE the delivery month.
# e.g. CLM26 (June delivery) expires around May 20 — so the roll from M→N
# happens in May, not June.  We use day-20 as the roll trigger.
#
# Two groups with the same prior-month logic but different roll days:
#   Energy  (CL, NG, RB, HO): FND ~20th of the prior month
#   Metals  (GC, SI, HG, PL): FND = last business day of prior month (~28-31st)
#                              but traders roll ~day-20 of prior month for liquidity
_PRIOR_MONTH_ROLL: frozenset[str] = frozenset({'CL', 'NG', 'RB', 'HO'})
_METALS_PRIOR_ROLL: frozenset[str] = frozenset({'GC', 'SI', 'HG', 'PL'})
_ENERGY_ROLL_DAY  = 20   # NYMEX energy contracts expire ~20th of prior month
_METALS_ROLL_DAY  = 20   # Metals FND is ~EOM of prior month; roll at day 20 for liquidity


def front_month_code(base: str, ref_date: datetime | None = None) -> str:
    """
    Return the current front-month contract symbol for a futures root.
    e.g. front_month_code('/ES')  →  '/ESM26'
         front_month_code('/ZC')  →  '/ZCN26'  (Jul — no June corn contract)
         front_month_code('/CL')  →  '/CLN26'  (Jul — June contract expired ~May 20)
         front_month_code('/GC')  →  '/GCQ26'  (Aug — June Gold FND ~May 29, roll day 20)

    Three roll modes
    ────────────────
    Standard (equity index, grains):
        Roll happens ON or AFTER the 15th of the delivery month itself.

    Prior-month energy (CL, NG, RB, HO):
        The contract expires in the month BEFORE delivery, around the 20th.
        e.g. CLM26 (June delivery) expires ~May 20, so on May 25 the front
        month is already CLN26 (July delivery).

    Prior-month metals (GC, SI, HG, PL):
        FND is the last business day of the month BEFORE delivery.
        e.g. GCM26 (June Gold) FND ~= May 29; we roll from day 20 of May
        to ensure we're always quoting the liquid contract.
    """
    ROLL_DAY     = 15
    now          = ref_date or datetime.now(ZoneInfo('America/New_York'))
    m, y         = now.month, now.year

    # Strip exchange suffix, then derive the bare root (e.g. '/ZC' → 'ZC')
    root         = base.split(':')[0]     # '/ES:XCME' → '/ES'
    bare         = root.lstrip('/')       # '/ZC'      → 'ZC'
    schedule     = FUTURES_SCHEDULES.get(bare, QUARTERLY)
    prior_roll   = bare in _PRIOR_MONTH_ROLL or bare in _METALS_PRIOR_ROLL
    if bare in _METALS_PRIOR_ROLL:
        roll_day = _METALS_ROLL_DAY
    elif bare in _PRIOR_MONTH_ROLL:
        roll_day = _ENERGY_ROLL_DAY
    else:
        roll_day = ROLL_DAY

    chosen_m = None
    chosen_y = y

    for exp_m in schedule:
        if prior_roll:
            # The contract with delivery month exp_m expires in month (exp_m - 1).
            # It has already rolled if:
            #   • today's month is >= exp_m  (we're in or past the delivery month)
            #   • today's month == exp_m - 1  AND  today's day >= roll_day
            expire_month = exp_m - 1   # month in which THIS contract expires
            if expire_month < 1:
                # January delivery → expires in December of prior year; always
                # rolled relative to any current month >= Jan.
                already_rolled = True
            else:
                already_rolled = (
                    m > expire_month or
                    (m == expire_month and now.day >= roll_day)
                )
            if not already_rolled:
                chosen_m = exp_m
                break
        else:
            # Standard mode: contract expires in its own delivery month
            if exp_m > m:
                chosen_m = exp_m
                break
            if exp_m == m and now.day < roll_day:
                chosen_m = exp_m
                break

    if chosen_m is None:
        # All months this year exhausted → first contract month of next year
        chosen_m = schedule[0]
        chosen_y = y + 1

    code    = MONTH_CODES[chosen_m]
    year_2d = str(chosen_y)[-2:]
    return f'{root}{code}{year_2d}'

LETTER_TO_MONTH: dict[str, int] = {v: k for k, v in MONTH_CODES.items()}


def next_contract_month(base: str, current_contract: str) -> str:
    """Return the next listed contract month after current_contract.

    e.g. next_contract_month('/GC', '/GCQ26') → '/GCV26' (Oct)
         next_contract_month('/ES', '/ESM26') → '/ESU26' (Sep)

    Wraps to January of the next year if no more months remain.
    """
    root     = base.split(':')[0]          # '/GC'
    bare     = root.lstrip('/')            # 'GC'
    schedule = FUTURES_SCHEDULES.get(bare, QUARTERLY)

    # Parse current contract: strip root prefix → 'Q26'
    rest = current_contract[len(root):]    # e.g. 'Q26'
    if len(rest) < 3:
        return current_contract            # can't parse — return unchanged

    month_letter = rest[0]                 # 'Q'
    year_2d      = rest[1:]                # '26'
    cur_month    = LETTER_TO_MONTH.get(month_letter)
    if cur_month is None:
        return current_contract

    cur_year = 2000 + int(year_2d)

    # Find next month in schedule after cur_month (same year)
    for m in schedule:
        if m > cur_month:
            return f'{root}{MONTH_CODES[m]}{year_2d}'

    # Wrap to next year — first listed month
    next_year = cur_year + 1
    return f'{root}{MONTH_CODES[schedule[0]]}{str(next_year)[-2:]}'


load_dotenv()

PRICE_HISTORY_URL = 'https://api.schwabapi.com/marketdata/v1/pricehistory'
QUOTES_URL        = 'https://api.schwabapi.com/marketdata/v1/quotes'
TOKEN_URL         = 'https://api.schwabapi.com/v1/oauth/token'

API_KEY    = os.environ['SCHWAB_API_KEY']
API_SECRET = os.environ['SCHWAB_API_SECRET']

# In-memory token cache + lock to prevent concurrent refresh races.
# refresh_all_1min() dispatches 16 threads simultaneously; without the lock
# they all see an expired token and all call _refresh_access_token() at once,
# causing Schwab to reject duplicate refresh requests with 400/403.
_token_cache = {
    'access_token' : None,
    'refresh_token': os.environ.get('SCHWAB_REFRESH_TOKEN', ''),
    'expires_at'   : 0,
}
_token_lock = threading.Lock()

# Optional callback — called with the new refresh token whenever Schwab rotates it.
# Set this from main.py to persist tokens across restarts (e.g. db.cache_set).
_on_token_refreshed = None


def set_token_refresh_callback(fn) -> None:
    """Register a callback(new_refresh_token: str) invoked on every token rotation."""
    global _on_token_refreshed
    _on_token_refreshed = fn


def _refresh_access_token() -> str:
    """Use refresh token to get a new access token (must be called under _token_lock)."""
    import logging as _log
    creds = base64.b64encode(f'{API_KEY}:{API_SECRET}'.encode()).decode()
    rt = _token_cache['refresh_token']
    _log.getLogger(__name__).info(
        'Refreshing Schwab token — rt_preview=%s...%s (len=%d)',
        rt[:8], rt[-6:], len(rt)
    )
    r = requests.post(TOKEN_URL,
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type'   : 'refresh_token',
              'refresh_token': rt},
        timeout=15)
    if not r.ok:
        _log.getLogger(__name__).error(
            'Schwab token refresh FAILED %s: %s', r.status_code, r.text[:300]
        )
    r.raise_for_status()
    data = r.json()
    _token_cache['access_token'] = data['access_token']
    _token_cache['expires_at']   = time.time() + data.get('expires_in', 1800) - 120
    if 'refresh_token' in data:
        _token_cache['refresh_token'] = data['refresh_token']
        # Persist the new token so the next restart doesn't use a stale one
        if _on_token_refreshed:
            try:
                _on_token_refreshed(_token_cache['refresh_token'])
            except Exception:
                pass  # never crash on persist failure
    return _token_cache['access_token']


def _get_token() -> str:
    with _token_lock:
        if not _token_cache['access_token'] or time.time() >= _token_cache['expires_at']:
            return _refresh_access_token()
        return _token_cache['access_token']


def _headers() -> dict:
    return {'Authorization': f'Bearer {_get_token()}', 'accept': 'application/json'}


def get_quotes(symbols: list[str]) -> dict:
    """Return {symbol: {last, open, high, low, close, volume}}"""
    resp = requests.get(QUOTES_URL,
        headers=_headers(),
        params={'symbols': ','.join(symbols), 'fields': 'quote'},
        timeout=15)
    if resp.status_code == 401:
        _token_cache['expires_at'] = 0
        resp = requests.get(QUOTES_URL,
            headers=_headers(),
            params={'symbols': ','.join(symbols), 'fields': 'quote'},
            timeout=15)
    resp.raise_for_status()
    out = {}
    for sym, payload in resp.json().items():
        q = payload.get('quote', {})
        # Futures: never use mark as fallback — mark is synthetic (mid bid/ask).
        # When CME is closed lastPrice=0; caller will fall back to candle prev_close.
        # Equities: mark is a reliable mid-price during extended hours.
        is_futures = sym.startswith('/')
        last_price = (q.get('lastPrice') or 0) if is_futures else (q.get('lastPrice') or q.get('mark', 0))
        out[sym] = {
            'last'      : last_price,
            'open'      : q.get('openPrice', 0),
            'high'      : q.get('highPrice', 0),
            'low'       : q.get('lowPrice', 0),
            'close'     : q.get('closePrice', 0),
            'volume'    : q.get('totalVolume', 0),
            # Futures-specific: change from previous CME settlement price
            'net_change'    : q.get('netChange', 0),
            # Daily % change — used by MAG10 weighted pct calculation
            'net_pct_change': q.get('netPercentChangeInDouble', 0),
            # Company/instrument name — used to label ETF holdings in the panel
            'description': q.get('description', ''),
        }
    return out


def get_candles(symbol: str, lookback_days: int, freq_min: int = 30) -> list[dict]:
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    params = {
        'symbol'               : symbol,
        'frequencyType'        : 'minute',
        'frequency'            : freq_min,
        'startDate'            : int(start.timestamp() * 1000),
        'endDate'              : int(end.timestamp()   * 1000),
        'needExtendedHoursData': 'true',
    }
    resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 401:
        _token_cache['expires_at'] = 0
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=30)
    if not resp.ok:
        import logging; _log = logging.getLogger(__name__)
        # Schwab returns 400 on US market holidays: it adjusts endDate to the last
        # trading day, which can fall before our startDate.  Suppress the noise —
        # returning [] is the correct behaviour (no intraday data on a holiday).
        if resp.status_code == 400 and 'before startDate' in resp.text:
            _log.debug('get_candles(%s) skipped — market holiday (endDate < startDate)', symbol)
        else:
            _log.warning('get_candles(%s) HTTP %s: %s', symbol, resp.status_code, resp.text[:200])
        return []
    data = resp.json()
    if not data.get('candles'):
        import logging; logging.getLogger(__name__).warning(
            'get_candles(%s) empty — response: %s', symbol, str(data)[:300])
    resp.raise_for_status()
    return data.get('candles', [])


def get_daily_candles(symbol: str, lookback_days: int = 30) -> list[dict]:
    """Fetch daily RTH bars for a symbol.
    Uses periodType/period (not startDate/endDate) — Schwab requires periodType=month/year
    when frequencyType=daily; using startDate/endDate causes a 400 'periodType DAY' error."""
    # Map lookback to the smallest valid Schwab period that covers the range
    if lookback_days <= 31:
        period_type, period = 'month', 1
    elif lookback_days <= 62:
        period_type, period = 'month', 2
    elif lookback_days <= 93:
        period_type, period = 'month', 3
    elif lookback_days <= 186:
        period_type, period = 'month', 6
    else:
        period_type, period = 'year', 1
    params = {
        'symbol'               : symbol,
        'periodType'           : period_type,
        'period'               : period,
        'frequencyType'        : 'daily',
        'frequency'            : 1,
        'needExtendedHoursData': 'false',
    }
    resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if resp.status_code == 401:
        _token_cache['expires_at'] = 0
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if not resp.ok:
        import logging; logging.getLogger(__name__).warning(
            'get_daily_candles(%s) HTTP %s: %s', symbol, resp.status_code, resp.text[:200])
        return []
    return resp.json().get('candles', [])


def get_session_bars(symbol: str) -> list[dict]:
    """Fetch 1-min bars from midnight ET today — used for VWAP/POC computation."""
    now_et     = datetime.now(ZoneInfo('America/New_York'))
    midnight   = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms   = int(midnight.astimezone(timezone.utc).timestamp() * 1000)
    end_ms     = int(datetime.now(timezone.utc).timestamp() * 1000)
    params = {
        'symbol'               : symbol,
        'frequencyType'        : 'minute',
        'frequency'            : 1,
        'startDate'            : start_ms,
        'endDate'              : end_ms,
        'needExtendedHoursData': 'true',
    }
    resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if resp.status_code == 401:
        _token_cache['expires_at'] = 0
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if not resp.ok:
        return []
    return resp.json().get('candles', [])


def get_current_hour_ohlc(symbol: str) -> dict | None:
    """Fetch the current ET hour's running OHLC from 1-min bars."""
    from zoneinfo import ZoneInfo
    now_et     = datetime.now(ZoneInfo('America/New_York'))
    hour_start = now_et.replace(minute=0, second=0, microsecond=0)
    start_ms   = int(hour_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms     = int(datetime.now(timezone.utc).timestamp() * 1000)
    params = {
        'symbol'               : symbol,
        'frequencyType'        : 'minute',
        'frequency'            : 1,
        'startDate'            : start_ms,
        'endDate'              : end_ms,
        'needExtendedHoursData': 'true',
    }
    resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if resp.status_code == 401:
        _token_cache['expires_at'] = 0
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=15)
    if not resp.ok:
        return None
    candles = resp.json().get('candles', [])
    if not candles:
        return None
    return {
        'open'  : candles[0]['open'],
        'high'  : max(c['high']   for c in candles),
        'low'   : min(c['low']    for c in candles),
        'close' : candles[-1]['close'],
        'volume': sum(c['volume'] for c in candles),
    }
