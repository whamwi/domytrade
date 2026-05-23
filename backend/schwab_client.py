"""
Schwab API client — quotes and intraday candles.
Token management is self-contained: reads from env vars, refreshes in memory.
No dependency on local token.json or market_hours.py.
"""
import os, time, base64, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

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
        out[sym] = {
            'last'      : q.get('lastPrice') or q.get('mark', 0),
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
    resp.raise_for_status()
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
