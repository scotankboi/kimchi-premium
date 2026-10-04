"""Common candle schema shared by every exchange fetcher.

Columns: open_time, close_time (both tz-aware UTC), open, high, low, close,
volume (base asset), quote_volume (KRW on Upbit, USDT on Binance).
A price in `close` is the last trade at `close_time`.
"""
from pathlib import Path

import pandas as pd

COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume", "quote_volume"]
STEPS = {"1d": pd.Timedelta(days=1), "1h": pd.Timedelta(hours=1)}


def to_utc(values) -> pd.Series:
    """Parse to tz-aware UTC with one fixed resolution so merges never mix units."""
    return pd.Series(pd.to_datetime(values, utc=True)).dt.as_unit("ns")


def utc_ts(value) -> pd.Timestamp:
    """Timestamp in UTC; naive inputs are read as UTC."""
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def last_closed(end, interval: str) -> pd.Timestamp:
    """Latest boundary such that every candle opening before it has closed."""
    stop = pd.Timestamp.now(tz="UTC")
    if end is not None:
        stop = min(utc_ts(end), stop)
    return stop.floor(STEPS[interval])


def finalize(df: pd.DataFrame, interval: str, start: pd.Timestamp, stop: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """Sort, clip to [start, stop), drop duplicate open times. Returns (frame, n_duplicates)."""
    df = df.copy()
    df["open_time"] = to_utc(df["open_time"])
    df["close_time"] = df["open_time"] + STEPS[interval]
    for col in COLUMNS[2:]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df = df[(df["open_time"] >= start) & (df["open_time"] < stop)]
    n_dup = int(df.duplicated("open_time").sum())
    df = df.drop_duplicates("open_time", keep="first").sort_values("open_time").reset_index(drop=True)
    return df[COLUMNS], n_dup


def save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["open_time"] = to_utc(df["open_time"])
    df["close_time"] = to_utc(df["close_time"])
    return df
