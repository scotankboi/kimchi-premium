"""Align Upbit, Binance and FX into one daily table and one hourly table.

Timing choice (the largest error source in a naive build):
FRED's DEXKOUS is the USD/KRW rate at 12:00 New York time (16:00 or 17:00 UTC
depending on US daylight saving). Daily exchange candles close at 00:00 UTC,
7-8 hours later. Instead of pairing a midnight crypto close with a noon FX
rate, the daily table samples both exchanges' hourly closes at the FX fixing
moment itself. Only data at or before that moment is used (backward as-of).
"""
import pandas as pd

from . import metrics


def fixing_moment(dates: pd.Series, fixing_time: str, tz: str) -> pd.Series:
    """Calendar dates -> tz-aware UTC timestamps of the FX fixing on each date."""
    hh, mm = (int(x) for x in fixing_time.split(":"))
    local = pd.Series(dates).reset_index(drop=True) + pd.Timedelta(hours=hh, minutes=mm)
    return local.dt.tz_localize(tz).dt.tz_convert("UTC").dt.as_unit("ns")


def fx_fixings(fx: pd.DataFrame, fixing_time: str, tz: str) -> pd.DataFrame:
    """FRED daily rows -> the UTC moment each rate was fixed."""
    return pd.DataFrame({
        "fx_date": fx["date"].reset_index(drop=True).dt.as_unit("ns"),
        "fix_ts": fixing_moment(fx["date"], fixing_time, tz),
        "usdkrw": fx["usdkrw"].reset_index(drop=True),
    })


def price_at(candles: pd.DataFrame, ts: pd.Series, tolerance_hours: int) -> pd.DataFrame:
    """Last close at or before each ts, if no older than the tolerance.

    Returns a frame aligned with `ts` (0..n-1 index): close, close_time.
    """
    left = pd.DataFrame({"ts": pd.Series(ts).reset_index(drop=True)})
    if not left["ts"].is_monotonic_increasing:
        raise ValueError("sample times must be sorted")
    right = candles[["close_time", "close"]].sort_values("close_time").reset_index(drop=True)
    out = pd.merge_asof(left, right, left_on="ts", right_on="close_time", direction="backward",
                        tolerance=pd.Timedelta(hours=tolerance_hours))
    return out[["close", "close_time"]]


def trailing_24h_value(candles: pd.DataFrame) -> pd.DataFrame:
    """Quote-currency trading value over the 24h ending at each candle close."""
    s = candles.set_index("close_time")["quote_volume"].sort_index().rolling("24h").sum()
    return pd.DataFrame({"close_time": s.index, "close": s.to_numpy()})


def _span(coin_h: pd.DataFrame, binance_h: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    first = max(coin_h["close_time"].min(), binance_h["close_time"].min())
    last = min(coin_h["close_time"].max(), binance_h["close_time"].max())
    return first, last


def build_daily(coin_h: pd.DataFrame, usdt_h: pd.DataFrame, binance_h: pd.DataFrame,
                fx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    fx_cfg, tol = cfg["fx"], cfg["sampling"]["price_tolerance_hours"]
    fixings = fx_fixings(fx, fx_cfg["fixing_time"], fx_cfg["fixing_tz"])
    first, last = _span(coin_h, binance_h)

    days = pd.Series(pd.date_range(first.tz_convert(None).normalize(),
                                   last.tz_convert(None).normalize(), freq="D").as_unit("ns"))
    sample_ts = fixing_moment(days, fx_cfg["fixing_time"], fx_cfg["fixing_tz"])
    keep = ((sample_ts >= first) & (sample_ts <= last)).to_numpy()
    df = pd.DataFrame({"date": days[keep].reset_index(drop=True),
                       "sample_ts_utc": sample_ts[keep].reset_index(drop=True)})

    # FX: the fixing of that calendar date, else the latest earlier one (weekend/holiday), flagged
    fx_match = pd.merge_asof(df[["date"]], fixings[["fx_date", "usdkrw"]],
                             left_on="date", right_on="fx_date", direction="backward")
    df["fx_date"] = fx_match["fx_date"]
    df["fx_age_days"] = (df["date"] - df["fx_date"]).dt.days
    df["usdkrw"] = fx_match["usdkrw"].where(df["fx_age_days"] <= fx_cfg["max_age_days"])

    ages = {}
    for name, candles in (("coin_krw", coin_h), ("coin_usdt", binance_h), ("usdt_krw", usdt_h)):
        p = price_at(candles, df["sample_ts_utc"], tol)
        df[name] = p["close"]
        ages[name] = (df["sample_ts_utc"] - p["close_time"]).dt.total_seconds() / 3600
    # USDT/KRW is legitimately absent before its listing, so only the coin legs define staleness
    df["price_age_h"] = pd.concat([ages["coin_krw"], ages["coin_usdt"]], axis=1).max(axis=1, skipna=False)

    df[["kp", "crypto_prem", "usdt_prem"]] = metrics.decompose(
        df["coin_krw"], df["coin_usdt"], df["usdt_krw"], df["usdkrw"])

    # Same premium sampled at the 00:00 UTC daily close instead (same FX): measures how much the
    # sampling moment alone moves the number. Reported in the quality report, not used downstream.
    close_ts = (df["date"] + pd.Timedelta(days=1)).dt.tz_localize("UTC").dt.as_unit("ns")
    df["kp_utc0"] = metrics.kimchi_premium(price_at(coin_h, close_ts, tol)["close"],
                                           price_at(binance_h, close_ts, tol)["close"], df["usdkrw"])
    df.loc[close_ts > last, "kp_utc0"] = float("nan")

    df["upbit_value_24h_krw"] = price_at(trailing_24h_value(coin_h), df["sample_ts_utc"], tol)["close"]
    return df


def build_hourly(coin_h: pd.DataFrame, usdt_h: pd.DataFrame, binance_h: pd.DataFrame,
                 fx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per hour (close time). Missing candles stay NaN; FX is the latest fixing already made."""
    fx_cfg = cfg["fx"]
    fixings = fx_fixings(fx, fx_cfg["fixing_time"], fx_cfg["fixing_tz"])
    first, last = _span(coin_h, binance_h)
    df = pd.DataFrame({"ts_utc": pd.date_range(first, last, freq="h").as_unit("ns")})

    for name, candles in (("coin_krw", coin_h), ("coin_usdt", binance_h), ("usdt_krw", usdt_h)):
        df[name] = df["ts_utc"].map(candles.set_index("close_time")["close"])
    df["upbit_value_krw"] = df["ts_utc"].map(coin_h.set_index("close_time")["quote_volume"])

    fx_match = pd.merge_asof(df[["ts_utc"]], fixings[["fix_ts", "usdkrw"]],
                             left_on="ts_utc", right_on="fix_ts", direction="backward")
    df["fx_age_h"] = (df["ts_utc"] - fx_match["fix_ts"]).dt.total_seconds() / 3600
    df["usdkrw"] = fx_match["usdkrw"].where(df["fx_age_h"] <= fx_cfg["max_age_days"] * 24)

    df[["kp", "crypto_prem", "usdt_prem"]] = metrics.decompose(
        df["coin_krw"], df["coin_usdt"], df["usdt_krw"], df["usdkrw"])
    return df
