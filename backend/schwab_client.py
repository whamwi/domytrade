"""Schwab API client — quotes and intraday candles."""
import sys, time
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/Users/wassim')
from market_hours import get_valid_token

PRICE_HISTORY_URL = 'https://api.schwabapi.com/marketdata/v1/pricehistory'
QUOTES_URL        = 'https://api.schwabapi.com/marketdata/v1/quotes'


def _headers():
    return {'Authorization': f'Bearer {get_valid_token()}', 'accept': 'application/json'}


def get_quotes(symbols: list[str]) -> dict:
    """Return {symbol: {mark, lastPrice, openPrice, highPrice, lowPrice, ...}}"""
    # Schwab quotes accept comma-separated symbols
    resp = requests.get(QUOTES_URL,
        headers=_headers(),
        params={'symbols': ','.join(symbols), 'fields': 'quote'},
        timeout=15)
    if resp.status_code == 401:
        resp = requests.get(QUOTES_URL,
            headers=_headers(),
            params={'symbols': ','.join(symbols), 'fields': 'quote'},
            timeout=15)
    resp.raise_for_status()
    data = resp.json()
    out = {}
    for sym, payload in data.items():
        q = payload.get('quote', {})
        out[sym] = {
            'last'  : q.get('lastPrice') or q.get('mark', 0),
            'open'  : q.get('openPrice', 0),
            'high'  : q.get('highPrice', 0),
            'low'   : q.get('lowPrice', 0),
            'close' : q.get('closePrice', 0),
            'volume': q.get('totalVolume', 0),
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
        resp = requests.get(PRICE_HISTORY_URL, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get('candles', [])


def get_current_hour_ohlc(symbol: str) -> dict | None:
    """Fetch the current ET hour's running OHLC from 1-min bars."""
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo('America/New_York'))
    hour_start_et = now_et.replace(minute=0, second=0, microsecond=0)
    hour_start_utc = hour_start_et.astimezone(timezone.utc)

    start_ms = int(hour_start_utc.timestamp() * 1000)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)

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
