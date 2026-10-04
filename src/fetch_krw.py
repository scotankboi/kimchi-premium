"""KRW exchange candles (Upbit, and Bithumb's Upbit-compatible v1 API), paged backwards with `to`.

API facts this relies on (checked against live responses):
- max 200 candles per call, newest first; `to` is exclusive on candle open time
- Upbit accepts `to` in UTC ("...Z"); Bithumb rejects "Z" and "+09:00" and reads a
  naive timestamp as KST, so the cursor is formatted per exchange (TO_TZ)
- candles carry candle_date_time_utc on both exchanges
- an hour with no trades has no candle at all (gaps are real, not errors)
"""
import logging

import pandas as pd

from .candles import finalize, last_closed, utc_ts
from .net import HttpClient

log = logging.getLogger(__name__)
PATHS = {"1d": "candles/days", "1h": "candles/minutes/60"}
TO_TZ = {"upbit": "UTC", "bithumb": "Asia/Seoul"}


def format_to(cursor: pd.Timestamp, exchange: str) -> str:
    if TO_TZ[exchange] == "UTC":
        return cursor.strftime("%Y-%m-%dT%H:%M:%SZ")
    return cursor.tz_convert(TO_TZ[exchange]).strftime("%Y-%m-%dT%H:%M:%S")


def fetch_candles(client: HttpClient, base_url: str, market: str, interval: str,
                  start, end=None, exchange: str = "upbit") -> pd.DataFrame:
    start = utc_ts(start)
    stop = last_closed(end, interval)
    cursor = stop
    rows: list[dict] = []
    while cursor > start:
        batch = client.get_json(f"{base_url}/{PATHS[interval]}", {
            "market": market, "count": 200, "to": format_to(cursor, exchange)})
        if not isinstance(batch, list):
            raise RuntimeError(f"{exchange} {market}: unexpected response {str(batch)[:200]}")
        if not batch:
            break  # before the listing date
        rows.extend(batch)
        oldest = min(pd.Timestamp(c["candle_date_time_utc"], tz="UTC") for c in batch)
        if oldest >= cursor:
            raise RuntimeError(f"{exchange} {market}: pagination stuck at {cursor}")
        cursor = oldest
    if not rows:
        raise RuntimeError(f"{exchange} {market} {interval}: no candles between {start} and {stop}")
    raw = pd.DataFrame(rows)
    df = pd.DataFrame({
        "open_time": raw["candle_date_time_utc"],
        "open": raw["opening_price"],
        "high": raw["high_price"],
        "low": raw["low_price"],
        "close": raw["trade_price"],
        "volume": raw["candle_acc_trade_volume"],
        "quote_volume": raw["candle_acc_trade_price"],
    })
    df, n_dup = finalize(df, interval, start, stop)
    if n_dup:
        log.warning("%s %s %s: dropped %d duplicate candles", exchange, market, interval, n_dup)
    log.info("%s %s %s: %d candles %s -> %s", exchange, market, interval, len(df),
             df["open_time"].iloc[0], df["open_time"].iloc[-1])
    return df

