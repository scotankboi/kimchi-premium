"""Data-quality checks. Nothing is silently dropped: problems are counted and listed."""
import numpy as np
import pandas as pd

from .candles import STEPS


def _ts(x) -> str | None:
    return None if pd.isna(x) else pd.Timestamp(x).isoformat()


def candle_report(df: pd.DataFrame, interval: str, max_abs_return: float, top: int = 10) -> dict:
    step = STEPS[interval]
    t = df["open_time"]
    diffs = t.diff()
    gap_rows = diffs[diffs > step]
    gaps = sorted(
        ({"after": _ts(t[i - 1]), "before": _ts(t[i]), "missing": int(d / step) - 1} for i, d in gap_rows.items()),
        key=lambda g: -g["missing"])
    bad_ohlc = ((df[["open", "high", "low", "close"]] <= 0).any(axis=1)
                | (df["low"] > df["high"]) | (df["close"] > df["high"]) | (df["close"] < df["low"]))
    # returns only between adjacent candles, so a gap is not mistaken for a jump
    ret = df["close"].pct_change().where(diffs == step)
    extreme = ret[ret.abs() > max_abs_return].sort_values(key=np.abs, ascending=False)
    return {
        "rows": len(df),
        "first_open": _ts(t.iloc[0]),
        "last_open": _ts(t.iloc[-1]),
        "expected_rows": int((t.iloc[-1] - t.iloc[0]) / step) + 1,
        "missing_candles": int(sum(g["missing"] for g in gaps)),
        "gap_count": len(gaps),
        "largest_gaps": gaps[:top],
        "duplicate_open_times": int(t.duplicated().sum()),
        "bad_ohlc_rows": int(bad_ohlc.sum()),
        "extreme_returns": [{"open_time": _ts(t[i]), "return": round(float(r), 4)} for i, r in extreme.head(top).items()],
        "extreme_return_count": int(len(extreme)),
    }


def daily_vs_hourly(daily: pd.DataFrame, hourly: pd.DataFrame) -> dict:
    """The exchange's own daily close must equal the last hourly close of that UTC day."""
    last_hour = hourly.set_index("close_time")["close"]
    h = last_hour.reindex(daily["close_time"]).to_numpy()
    rel = np.abs(daily["close"].to_numpy() / h - 1)
    mism = daily.loc[rel > 1e-9, ["open_time", "close"]].assign(hourly_close=h[rel > 1e-9])
    covered = ~np.isnan(rel)
    daily_vol = daily.set_index("close_time")["volume"]
    hourly_vol = hourly.set_index("close_time")["volume"].resample("1D", closed="right", label="right").sum()
    vol_rel = (daily_vol / hourly_vol.reindex(daily_vol.index) - 1).abs()
    return {
        "days_compared": int(covered.sum()),
        "days_without_23h_candle": int((~covered).sum()),
        "close_mismatches": int((rel[covered] > 1e-9).sum()),
        "close_mismatch_examples": [
            {"day": _ts(r.open_time), "daily_close": r.close, "hourly_close": r.hourly_close}
            for r in mism.head(5).itertuples()],
        "volume_rel_diff_p99": round(float(vol_rel.quantile(0.99)), 6),
        "volume_rel_diff_max": round(float(vol_rel.max()), 6),
    }


def dataset_report(daily: pd.DataFrame, hourly: pd.DataFrame, cfg: dict) -> dict:
    q = cfg["quality"]
    kp = daily["kp"]
    missing = daily[kp.isna()]
    identity = (1 + daily["kp"]) - (1 + daily["crypto_prem"]) * (1 + daily["usdt_prem"])
    sens = (daily["kp_utc0"] - daily["kp"]).dropna()
    sens_abs = sens.abs()
    big_kp = daily.loc[kp.abs() > q["max_abs_premium"], ["date", "kp"]]
    big_usdt = daily.loc[daily["usdt_prem"].abs() > q["max_abs_usdt_premium"], ["date", "usdt_prem"]]
    usdt_rows = daily["usdt_krw"].notna()
    return {
        "daily": {
            "rows": len(daily),
            "first_date": _ts(daily["date"].iloc[0]),
            "last_date": _ts(daily["date"].iloc[-1]),
            "rows_without_kp": len(missing),
            "rows_without_kp_reason": {
                "fx_missing_or_too_old": int(missing["usdkrw"].isna().sum()),
                "price_missing_beyond_tolerance": int(missing[["coin_krw", "coin_usdt"]].isna().any(axis=1).sum()),
            },
            "fx_stale_days": int((daily["fx_age_days"] > 0).sum()),
            "fx_age_days_max_used": int(daily.loc[daily["usdkrw"].notna(), "fx_age_days"].max()),
            "price_fallback_days": int((daily["price_age_h"] > 0).sum()),
            "price_age_h_max": float(daily["price_age_h"].max()),
            "usdt_krw_first_date": _ts(daily.loc[usdt_rows, "date"].min()),
            "identity_max_abs_error": float(identity.abs().max()) if identity.notna().any() else None,
            "premium_outliers": [{"date": _ts(r.date), "kp": round(r.kp, 4)} for r in big_kp.itertuples()],
            "usdt_premium_outliers": [{"date": _ts(r.date), "usdt_prem": round(r.usdt_prem, 4)}
                                      for r in big_usdt.itertuples()],
        },
        "sampling_time_sensitivity": {
            "what": "kp at 00:00 UTC daily close minus kp at NY-noon FX fixing, same FX rate",
            "days": int(len(sens)),
            "median_abs_pp": round(float(sens_abs.median()) * 100, 3),
            "p95_abs_pp": round(float(sens_abs.quantile(0.95)) * 100, 3),
            "max_abs_pp": round(float(sens_abs.max()) * 100, 3),
            "max_abs_date": _ts(daily.loc[sens_abs.idxmax(), "date"]) if len(sens) else None,
        },
        "hourly": {
            "rows": len(hourly),
            "first_ts": _ts(hourly["ts_utc"].iloc[0]),
            "last_ts": _ts(hourly["ts_utc"].iloc[-1]),
            "rows_without_kp": int(hourly["kp"].isna().sum()),
            "rows_missing_coin_krw": int(hourly["coin_krw"].isna().sum()),
            "rows_missing_coin_usdt": int(hourly["coin_usdt"].isna().sum()),
            "rows_fx_too_old": int(hourly["usdkrw"].isna().sum()),
        },
    }


def cross_exchange_alignment(a: pd.DataFrame, b: pd.DataFrame, lags=(-9, -1, 0, 1, 9)) -> dict:
    """Two exchanges' hourly closes for the same market. A timezone slip in either fetcher would move
    the best return correlation away from lag 0 and widen the same-hour price gap."""
    pa = a.set_index("close_time")["close"]
    pb = b.set_index("close_time")["close"]
    j = pd.concat({"a": pa, "b": pb}, axis=1, sort=True).dropna()
    gap = (j["b"] / j["a"] - 1).abs()
    ra, rb = np.log(pa).diff(), np.log(pb).diff()
    corr = {int(L): round(float(ra.corr(rb.shift(L))), 4) for L in lags}
    return {"common_hours": len(j), "median_abs_gap": round(float(gap.median()), 6),
            "p99_abs_gap": round(float(gap.quantile(0.99)), 6), "return_corr_by_lag_hours": corr,
            "best_lag_hours": max(corr, key=corr.get)}
