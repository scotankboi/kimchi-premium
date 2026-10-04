"""Alignment semantics. Synthetic prices encode their own timestamp, so a test can
read off exactly which candle and which FX fixing each row used."""
import copy

import numpy as np
import pandas as pd
import pytest

from src.build_dataset import build_daily, build_hourly
from src.candles import utc_ts
from src.config import load_config

EPOCH = pd.Timestamp("2024-01-01", tz="UTC")


def hours_since_epoch(ts) -> float:
    return (utc_ts(ts) - EPOCH) / pd.Timedelta(hours=1)


def candles(start="2024-01-04", end="2024-03-14", price=hours_since_epoch):
    opens = pd.date_range(start, end, freq="h", tz="UTC", inclusive="left").as_unit("ns")
    close_time = opens + pd.Timedelta(hours=1)
    close = np.array([price(t) for t in close_time], dtype=float)
    return pd.DataFrame({"open_time": opens, "close_time": close_time, "open": close, "high": close,
                         "low": close, "close": close, "volume": 1.0, "quote_volume": 10.0})


def fx_weekdays(start="2024-01-01", end="2024-03-14"):
    days = pd.bdate_range(start, end).as_unit("ns")          # no weekend fixings, like FRED
    return pd.DataFrame({"date": days, "usdkrw": 1300.0 + days.day})  # value encodes the fixing date


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def inputs():
    coin = candles()                                   # coin_krw = hours since epoch at close
    usd = candles(price=lambda t: 1.0)                 # coin_usdt = 1
    usdt = candles(start="2024-02-01", price=lambda t: 1.0)
    return coin, usdt, usd, fx_weekdays()


def row(daily, date):
    return daily.set_index("date").loc[pd.Timestamp(date)]


def test_samples_at_ny_noon_across_daylight_saving(cfg, inputs):
    coin, usdt, usd, fx = inputs
    daily = build_daily(coin, usdt, usd, fx, cfg)
    winter, summer = row(daily, "2024-03-08"), row(daily, "2024-03-11")  # US DST starts 2024-03-10
    assert winter["sample_ts_utc"] == pd.Timestamp("2024-03-08 17:00", tz="UTC")
    assert summer["sample_ts_utc"] == pd.Timestamp("2024-03-11 16:00", tz="UTC")
    assert winter["coin_krw"] == hours_since_epoch("2024-03-08 17:00")  # candle closing exactly at noon NY
    assert summer["coin_krw"] == hours_since_epoch("2024-03-11 16:00")
    assert winter["price_age_h"] == 0


def test_weekend_uses_friday_fx_and_is_flagged(cfg, inputs):
    coin, usdt, usd, fx = inputs
    daily = build_daily(coin, usdt, usd, fx, cfg)
    fri, sat, sun = row(daily, "2024-02-09"), row(daily, "2024-02-10"), row(daily, "2024-02-11")
    assert fri["usdkrw"] == 1309 and fri["fx_age_days"] == 0
    assert sat["usdkrw"] == 1309 and sat["fx_age_days"] == 1
    assert sun["usdkrw"] == 1309 and sun["fx_age_days"] == 2
    assert sun["kp"] == pytest.approx(sun["coin_krw"] / 1309 - 1)


def test_fx_older_than_limit_is_not_forward_filled(cfg, inputs):
    coin, usdt, usd, fx = inputs
    fx = fx[(fx["date"] < "2024-02-12") | (fx["date"] > "2024-02-23")]  # two weeks without fixings
    daily = build_daily(coin, usdt, usd, fx, cfg)
    assert row(daily, "2024-02-13")["fx_age_days"] == 4 and not np.isnan(row(daily, "2024-02-13")["kp"])
    assert np.isnan(row(daily, "2024-02-14")["usdkrw"]) and np.isnan(row(daily, "2024-02-14")["kp"])


def test_missing_candle_falls_back_within_tolerance_only(cfg, inputs):
    coin, usdt, usd, fx = inputs
    noon = pd.Timestamp("2024-02-06 17:00", tz="UTC")
    coin = coin[coin["close_time"] != noon]                         # one hour without trades
    later = pd.Timestamp("2024-02-07 17:00", tz="UTC")
    coin = coin[(coin["close_time"] > later) | (coin["close_time"] <= later - pd.Timedelta(hours=5))]
    daily = build_daily(coin, usdt, usd, fx, cfg)
    one = row(daily, "2024-02-06")
    assert one["coin_krw"] == hours_since_epoch(noon - pd.Timedelta(hours=1)) and one["price_age_h"] == 1
    assert np.isnan(row(daily, "2024-02-07")["coin_krw"])           # 5h gap > 3h tolerance


def test_hourly_fx_has_no_look_ahead(cfg, inputs):
    coin, usdt, usd, fx = inputs
    hourly = build_hourly(coin, usdt, usd, fx, cfg).set_index("ts_utc")
    before = hourly.loc[pd.Timestamp("2024-02-06 16:00", tz="UTC")]  # an hour before the Feb-6 fixing
    at = hourly.loc[pd.Timestamp("2024-02-06 17:00", tz="UTC")]
    assert before["usdkrw"] == 1305 and before["fx_age_h"] == 23      # still Monday Feb-5's fixing
    assert at["usdkrw"] == 1306 and at["fx_age_h"] == 0


def test_decomposition_only_after_usdt_listing_and_identity_holds(cfg, inputs):
    coin, usdt, usd, fx = inputs
    daily = build_daily(coin, usdt, usd, fx, cfg)
    assert daily.loc[daily["date"] < "2024-02-01", "usdt_prem"].isna().all()
    post = daily[daily["date"] >= "2024-02-02"].dropna(subset=["kp"])
    assert post["usdt_prem"].notna().all()
    err = (1 + post["kp"]) - (1 + post["crypto_prem"]) * (1 + post["usdt_prem"])
    assert err.abs().max() < 1e-12


def test_utc_close_variant_uses_next_midnight(cfg, inputs):
    coin, usdt, usd, fx = inputs
    r = row(build_daily(coin, usdt, usd, fx, cfg), "2024-02-06")
    assert r["kp_utc0"] == pytest.approx(hours_since_epoch("2024-02-07 00:00") / r["usdkrw"] - 1)


def test_config_change_changes_result(cfg, inputs):
    """A config value must drive the calculation: tolerance 0 turns the 1h fallback into NaN."""
    coin, usdt, usd, fx = inputs
    coin = coin[coin["close_time"] != pd.Timestamp("2024-02-06 17:00", tz="UTC")]
    strict = copy.deepcopy(cfg)
    strict["sampling"]["price_tolerance_hours"] = 0
    assert not np.isnan(row(build_daily(coin, usdt, usd, fx, cfg), "2024-02-06")["coin_krw"])
    assert np.isnan(row(build_daily(coin, usdt, usd, fx, strict), "2024-02-06")["coin_krw"])
