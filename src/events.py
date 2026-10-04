"""Check each candidate event against the hourly premium before it goes on a chart.

For each event: the mean premium over the `hours_before` hours before it (the pre-event level),
then the highest and lowest hourly values in the `hours_after` hours after it. An event is
annotated only if the premium moved at least `min_move_pp` percentage points away from the
pre-event level after the event; otherwise it is dropped. Only post-event hours count, so a
move that was already under way before the event cannot qualify it.

The crypto-specific premium (FX-free) is reported alongside wherever it exists, because the
hourly KP uses the latest FX fixing, which can be a day old (more over a weekend) during an
intraday shock. The age of that fixing is reported at the hours of the post-event high and low.

Where Upbit USDT/KRW trades, the peak of its hourly trading value after the event is reported
relative to its trailing median, with the 99th percentile of that ratio over all hours for scale.

Timestamps are candle close times, so the first post-event hour is the candle that contains the event.
"""
import numpy as np
import pandas as pd

EMPTY = {"pre": np.nan, "high": np.nan, "high_at": pd.NaT, "low": np.nan, "low_at": pd.NaT,
         "move": np.nan, "side": None, "extreme_at": pd.NaT}


def _window_stats(s: pd.Series, t0: pd.Timestamp, before: int, after: int) -> dict:
    pre = s[(s.index >= t0 - pd.Timedelta(hours=before)) & (s.index < t0)].dropna()
    post = s[(s.index > t0) & (s.index <= t0 + pd.Timedelta(hours=after))].dropna()
    if pre.empty or post.empty:
        return dict(EMPTY)
    level = pre.mean()
    up, down = post.max() - level, level - post.min()
    side = "high" if up >= down else "low"
    out = {"pre": level, "high": post.max(), "high_at": post.idxmax(), "low": post.min(), "low_at": post.idxmin(),
           "move": max(up, down), "side": side}
    out["extreme_at"] = out[f"{side}_at"]
    return out


def _get(s: pd.Series, t) -> float:
    return s.get(t, np.nan) if pd.notna(t) else np.nan


def turnover_ratio(candles: pd.DataFrame, baseline_days: int) -> pd.Series:
    """Hourly trading value / its median over the previous `baseline_days` (current hour excluded).
    The first `baseline_days` after listing have no full baseline and are left out."""
    s = candles.set_index("close_time")["quote_volume"].sort_index()
    ratio = s / s.rolling(f"{baseline_days}D", closed="left").median()
    return ratio[ratio.index >= s.index.min() + pd.Timedelta(days=baseline_days)]


def event_check(hourly: pd.DataFrame, cfg: dict, usdt_candles: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per candidate event. If Upbit USDT/KRW candles are given, also the peak of its hourly
    trading value relative to the trailing median after each event, and the 99th percentile of that
    ratio over all hours for scale."""
    c = cfg["events"]
    h = hourly.set_index("ts_utc")
    turn = turnover_ratio(usdt_candles, c["turnover_baseline_days"]) if usdt_candles is not None else None
    rows = []
    for ev in c["list"]:
        t0 = pd.Timestamp(ev["time_utc"], tz="UTC")
        kp = _window_stats(h["kp"], t0, c["hours_before"], c["hours_after"])
        cr = _window_stats(h["crypto_prem"], t0, c["hours_before"], c["hours_after"])
        at = {side: kp[f"{side}_at"] for side in ("high", "low")}
        rows.append({"label": ev["label"], "time_utc": t0, **{f"kp_{k}": v for k, v in kp.items()},
                     **{f"fx_age_h_at_kp_{side}": _get(h["fx_age_h"], t) for side, t in at.items()},
                     **{f"crypto_at_kp_{side}": _get(h["crypto_prem"], t) for side, t in at.items()},
                     **{f"crypto_{k}": v for k, v in cr.items() if k in ("pre", "high", "low")},
                     **_turnover_peak(turn, t0, c["hours_after"]),
                     "annotate": bool(kp["move"] >= c["min_move_pp"] / 100)})
    out = pd.DataFrame(rows)
    if turn is not None:
        out["usdt_value_ratio_p99_all_hours"] = turn.quantile(0.99)
    return out


def _turnover_peak(turn: pd.Series | None, t0: pd.Timestamp, after: int) -> dict:
    if turn is None:
        return {}
    w = turn[(turn.index > t0) & (turn.index <= t0 + pd.Timedelta(hours=after))].dropna()
    if w.empty:
        return {"usdt_value_ratio_max": np.nan, "usdt_value_ratio_max_at": pd.NaT}
    return {"usdt_value_ratio_max": w.max(), "usdt_value_ratio_max_at": w.idxmax()}
