"""Decomposition splice, drivers (no look-ahead, date-based lags, Newey-West) and the event filter."""
import numpy as np
import pandas as pd
import pytest

from src import decomposition, drivers, events
from src.config import load_config


def daily_frame(dates, usdt_from, kp=0.02, usdt=0.019, crypto=0.001):
    d = pd.DataFrame({"date": pd.to_datetime(dates)})
    has = d["date"] >= usdt_from
    d["usdt_krw"] = np.where(has, 1400.0, np.nan)
    d["kp"] = kp
    d["usdt_prem"] = np.where(has, usdt, np.nan)
    d["crypto_prem"] = np.where(has, crypto, np.nan)
    return d


def test_splice_switches_to_upbit_on_its_first_usdt_day():
    dates = pd.date_range("2024-01-01", "2024-01-10")
    up = daily_frame(dates, "2024-01-06", usdt=0.03)
    bt = daily_frame(dates, "2024-01-03", usdt=0.01)
    s = decomposition.splice(up, bt)
    assert s.loc[s["source"] == "bithumb", "date"].dt.day.tolist() == [3, 4, 5]
    assert s.loc[s["source"] == "upbit", "date"].dt.day.tolist() == [6, 7, 8, 9, 10]
    assert s["date"].is_unique


@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")  # constant series: corr is NaN
def test_overlap_stats_hand_values():
    dates = pd.date_range("2024-06-01", periods=4)
    up = daily_frame(dates, "2024-06-01")
    bt = daily_frame(dates, "2024-06-01")
    bt["usdt_prem"] = [0.019, 0.021, 0.017, 0.019]                 # |diff| = 0, .002, .002, 0
    o = decomposition.overlap_stats(up, bt).set_index("series")
    assert o.loc["usdt_prem", "days"] == 4
    assert o.loc["usdt_prem", "mean_abs_diff"] == pytest.approx(0.001)
    assert o.loc["kp", "mean_abs_diff"] == pytest.approx(0)


@pytest.fixture
def cfg():
    return load_config()


def feature_inputs():
    dates = pd.date_range("2024-01-01", "2024-06-30")
    daily = pd.DataFrame({"date": dates, "kp": 0.01, "coin_usdt": np.arange(len(dates), dtype=float) + 100,
                          "upbit_value_24h_krw": 1e9})
    fx = pd.DataFrame({"date": pd.bdate_range("2023-11-01", "2024-06-30")})
    fx["usdkrw"] = 1300 + np.sin(np.arange(len(fx))) * 5
    return daily, fx


def test_btc_return_lag_is_by_date_not_by_row(cfg):
    daily, fx = feature_inputs()
    daily = daily[daily["date"] != "2024-03-01"]                  # a missing day must not shift the lag
    f = drivers.features(daily, fx, cfg).set_index("date")
    t = pd.Timestamp("2024-03-15")
    price = lambda day: 100 + (day - pd.Timestamp("2024-01-01")).days
    assert f.loc[t, "btc_ret"] == pytest.approx(price(t) / price(t - pd.Timedelta(days=30)) - 1)


def test_fx_vol_uses_no_future_fixings(cfg):
    daily, fx = feature_inputs()
    base = drivers.features(daily, fx, cfg).set_index("date")["fx_vol"]
    fx2 = fx.copy()
    fx2.loc[fx2["date"] > "2024-04-01", "usdkrw"] *= 1.5          # change only the future
    changed = drivers.features(daily, fx2, cfg).set_index("date")["fx_vol"]
    assert base[:"2024-04-01"].equals(changed[:"2024-04-01"])
    assert not base["2024-04-02":].equals(changed["2024-04-02":])


def test_newey_west_lag0_is_white_hc0_and_beta_is_ols():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(200), rng.normal(size=(200, 2))])
    y = X @ np.array([1.0, 0.5, -0.3]) + rng.normal(size=200) * (1 + np.abs(X[:, 1]))
    beta, se = drivers.newey_west_ols(y, X, lags=0)
    assert beta == pytest.approx(np.linalg.lstsq(X, y, rcond=None)[0])
    u = y - X @ beta
    inv = np.linalg.inv(X.T @ X)
    hc0 = inv @ (X.T * u**2) @ X @ inv
    assert se == pytest.approx(np.sqrt(np.diag(hc0)))
    _, se3 = drivers.newey_west_ols(y, X, lags=3)
    assert se3.shape == se.shape and np.all(se3 > 0)


def test_event_filter_keeps_moves_and_drops_flat_periods(cfg):
    ts = pd.date_range("2024-01-01", "2024-01-10", freq="h", tz="UTC")
    kp = pd.Series(0.01, index=ts)
    kp[(ts >= "2024-01-05 03:00") & (ts < "2024-01-05 06:00")] = 0.05     # +4pp after the event
    hourly = pd.DataFrame({"ts_utc": ts, "kp": kp.to_numpy(), "crypto_prem": np.nan, "fx_age_h": 5.0})
    cfg = {**cfg, "events": {**cfg["events"], "list": [
        {"time_utc": "2024-01-05 00:00", "label": "moves"}, {"time_utc": "2024-01-08 00:00", "label": "flat"}]}}
    e = events.event_check(hourly, cfg).set_index("label")
    assert bool(e.loc["moves", "annotate"]) and e.loc["moves", "kp_move"] == pytest.approx(0.04)
    assert e.loc["moves", "kp_side"] == "high"
    assert e.loc["moves", "kp_extreme_at"] == pd.Timestamp("2024-01-05 03:00", tz="UTC")
    assert e.loc["moves", "fx_age_h_at_kp_high"] == 5.0
    assert not bool(e.loc["flat", "annotate"])


def test_event_filter_ignores_moves_before_the_event(cfg):
    """A spike in the pre-event hours raises the pre-event level but cannot itself qualify the event."""
    ts = pd.date_range("2024-01-01", "2024-01-10", freq="h", tz="UTC")
    kp = pd.Series(0.01, index=ts)
    kp[(ts >= "2024-01-04 20:00") & (ts < "2024-01-05 00:00")] = 0.08     # +7pp in the 4h before
    hourly = pd.DataFrame({"ts_utc": ts, "kp": kp.to_numpy(), "crypto_prem": np.nan, "fx_age_h": 5.0})
    cfg = {**cfg, "events": {**cfg["events"], "list": [{"time_utc": "2024-01-05 00:00", "label": "late"}]}}
    e = events.event_check(hourly, cfg).set_index("label")
    # pre level = (20 x 1% + 4 x 8%) / 24; afterwards the premium sits at 1%, a fall of 1.17pp
    assert e.loc["late", "kp_pre"] == pytest.approx((20 * 0.01 + 4 * 0.08) / 24)
    assert e.loc["late", "kp_high"] == pytest.approx(0.01)
    assert e.loc["late", "kp_move"] == pytest.approx((20 * 0.01 + 4 * 0.08) / 24 - 0.01)
    assert not bool(e.loc["late", "annotate"])


def test_turnover_ratio_uses_only_past_hours_and_skips_the_first_window():
    ts = pd.date_range("2024-06-01 01:00", periods=24 * 40, freq="h", tz="UTC")
    value = pd.Series(100.0, index=ts)
    value[ts == pd.Timestamp("2024-07-05 12:00", tz="UTC")] = 2_000.0          # one 20x hour
    candles = pd.DataFrame({"close_time": ts, "quote_volume": value.to_numpy()})
    r = events.turnover_ratio(candles, baseline_days=30)
    assert r.index.min() == ts.min() + pd.Timedelta(days=30)
    assert r[pd.Timestamp("2024-07-05 12:00", tz="UTC")] == pytest.approx(20.0)
    assert r[pd.Timestamp("2024-07-05 13:00", tz="UTC")] == pytest.approx(1.0)   # median ignores the spike
