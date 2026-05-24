"""
Schwab API client — quotes and intraday candles.
Token management is self-contained: reads from env vars, refreshes in memory.
No dependency on local token.json or market_hours.py.
"""
import os, time, base64, requests
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

def front_month_code(base: str, ref_date: datetime | None = None) -> str:
    """
    Return the current front-month contract symbol for a futures root.
    e.g. front_month_code('/ES')  →  '/ESM26'

    Uses quarterly roll schedule (H/M/U/Z).
    Assumes roll happens on the 15th of the expiry month (conservative — actual
    roll is 3rd Friday, ~15–21st; adjust ROLL_DAY if needed).
    """
    ROLL_DAY = 15
    now = ref_date or datetime.now(ZoneInfo('America/New_York'))
    m, y = now.month, now.year

    # Find next quarterly month that hasn't rolled yet
    for qm in QUARTERLY:
        if qm > m:
            break
        if qm == m and now.day < ROLL_DAY:
            break
    else:
        # All quarterly months this year have passed → first quarterly next year
        qm = QUARTERLY[0]
        y += 1

    code = MONTH_CODES[qm]
    year_2d = str(y)[-2:]
    # Strip exchange suffix if present, then re-append root
    root = base.split(':')[0]   # '/ES:XCME' → '/ES'
    return f'{root}{code}{year_2d}'

load_dotenv()

PRICE_HISTORY_URL = 'https://api.schwabapi.com/marketdata/v1/pricehistory'
QUOTES_URL        = 'https://api.schwabapi.com/marketdata/v1/quotes'
TOKEN_URL         = 'https://api.schwabapi.com/v1/oauth/token'

API_KEY    = os.environ['SCHWAB_API_KEY']
API_SECRET = os.environ['SCHWAB_API_SECRET']

# In-memory token cache
_token_cache = {
    'access_token' : None,
    'refresh_token': os.environ.get('SCHWAB_REFRESH_TOKEN', ''),
    'expires_at'   : 0,
}


def _refresh_access_token() -> str:
    """Use refresh token to get a new access token."""
    creds = base64.b64encode(f'{API_KEY}:{API_SECRET}'.encode()).decode()
    r = requests.post(TOKEN_URL,
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type'   : 'refresh_token',
              'refresh_token': _token_cache['refresh_token']},
        timeout=15)
    r.raise_for_status()
    data = r.json()
    _token_cache['access_token'] = data['access_token']
    _token_cache['expires_at']   = time.time() + data.get('expires_in', 1800) - 120
    if 'refresh_token' in data:
        _token_cache['refresh_token'] = data['refresh_token']
    return _token_cache['access_token']


def _get_token() -> str:
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
            'net_change': q.get('netChange', 0),
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
        import logging; logging.getLogger(__name__).warning(
            'get_candles(%s) HTTP %s: %s', symbol, resp.status_code, resp.text[:200])
        return []
    data = resp.json()
    if not data.get('candles'):
        import logging; logging.getLogger(__name__).warning(
            'get_candles(%s) empty — response: %s', symbol, str(data)[:300])
    resp.raise_for_status()
    return data.get('candles', [])


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
