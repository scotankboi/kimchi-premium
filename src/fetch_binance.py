"""Binance spot klines, paged forwards with startTime (1000 per call)."""
import logging

import pandas as pd
import requests

from .candles import finalize, last_closed, utc_ts
from .net import HttpClient

log = logging.getLogger(__name__)
LIMIT = 1000


def _fetch(client: HttpClient, base_url: str, symbol: str, interval: str,
           start: pd.Timestamp, stop: pd.Timestamp) -> list[list]:
    cursor = int(start.timestamp() * 1000)
    end_ms = int(stop.timestamp() * 1000) - 1  # only candles that opened before `stop` (i.e. closed)
    rows: list[list] = []
    while cursor <= end_ms:
        batch = client.get_json(f"{base_url}/api/v3/klines", {
            "symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": LIMIT})
        if not isinstance(batch, list):
            raise RuntimeError(f"Binance {symbol}: unexpected response {str(batch)[:200]}")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < LIMIT:
            break
        cursor = batch[-1][0] + 1
    return rows


def fetch_klines(client: HttpClient, base_urls: list[str], symbol: str, interval: str,
                 start, end=None) -> pd.DataFrame:
    start = utc_ts(start)
    stop = last_closed(end, interval)
    errors = []
    for base_url in base_urls:
        try:
            rows = _fetch(client, base_url, symbol, interval, start, stop)
            break
        except (requests.HTTPError, RuntimeError) as exc:  # e.g. HTTP 451 geo-block
            log.warning("Binance via %s failed (%s); trying next base URL", base_url, exc)
            errors.append(exc)
    else:
        raise RuntimeError(f"Binance {symbol}: all base URLs failed: {errors}")
    if not rows:
        raise RuntimeError(f"Binance {symbol} {interval}: no klines between {start} and {stop}")
    raw = pd.DataFrame(rows)
    df = pd.DataFrame({
        "open_time": pd.to_datetime(raw[0], unit="ms", utc=True),
        "open": raw[1], "high": raw[2], "low": raw[3], "close": raw[4],
        "volume": raw[5], "quote_volume": raw[7],
    })
    df, n_dup = finalize(df, interval, start, stop)
    if n_dup:
        log.warning("Binance %s %s: dropped %d duplicate klines", symbol, interval, n_dup)
    log.info("Binance %s %s: %d klines %s -> %s", symbol, interval, len(df),
             df["open_time"].iloc[0], df["open_time"].iloc[-1])
    return df


def parse_funding(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame({
        # fundingTime carries a few ms of jitter (…000, …001, …002); the event is on the hour
        "funding_time": pd.to_datetime(pd.Series([r["fundingTime"] for r in rows], dtype="int64"),
                                       unit="ms", utc=True).dt.floor("h").dt.as_unit("ns"),
        "rate": pd.to_numeric(pd.Series([r["fundingRate"] for r in rows])).astype(float),
    })
    return df.drop_duplicates("funding_time").sort_values("funding_time").reset_index(drop=True)


def fetch_funding(client: HttpClient, base_url: str, symbol: str, start, end=None) -> pd.DataFrame:
    """USD-M perpetual funding history. startTime must be a real date: startTime=0 is read as
    'no start' and returns only the latest records."""
    cursor = int(utc_ts(start).timestamp() * 1000)
    end_ms = int(last_closed(end, "1h").timestamp() * 1000)
    if cursor <= 0:
        raise ValueError("funding start must be after 1970")
    rows: list[dict] = []
    while cursor < end_ms:
        batch = client.get_json(f"{base_url}/fapi/v1/fundingRate",
                                {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": LIMIT})
        if not isinstance(batch, list):
            raise RuntimeError(f"Binance funding {symbol}: unexpected response {str(batch)[:200]}")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < LIMIT:
            break
        cursor = batch[-1]["fundingTime"] + 1
    df = parse_funding(rows)
    log.info("Binance funding %s: %d records %s -> %s", symbol, len(df), df["funding_time"].iloc[0],
             df["funding_time"].iloc[-1])
    return df
