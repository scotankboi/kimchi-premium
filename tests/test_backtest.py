"""Backtest economics on hand-built price paths."""
import copy

import numpy as np
import pandas as pd
import pytest

from src import backtest as bt
from src.config import load_config

FX = 1400.0


@pytest.fixture
def b():
    c = copy.deepcopy(load_config()["backtest"])
    c["fees"] = {"binance_spot_taker": 0.001, "binance_futures_taker": 0.0005, "upbit_trade": 0.0005,
                 "btc_withdrawal_btc": 0.0002, "usdt_withdrawal_usdt": 1.0, "remit_fee_usd": 25}
    c["transfer_hours"] = {"btc": 1, "usdt": 1}
    return c


def hourly(n=48, start="2024-06-10", premium=0.05, btc=50_000.0):
    ts = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "coin_usdt": btc, "coin_krw": btc * FX * (1 + premium),
                         "usdt_krw": FX * (1 + premium), "usdkrw": FX, "fx_age_h": 5.0})


NO_FUNDING = pd.DataFrame({"funding_time": pd.to_datetime(["2019-09-10 08:00"], utc=True), "rate": [0.0]})


def test_btc_path_hand_calculation(b):
    r = bt.btc_returns(hourly(), NO_FUNDING, b, fx_spread=0.01, hedged=False).iloc[0]
    size = 10_000
    btc = size * 0.999 / 50_000 - 0.0002                          # bought, minus withdrawal fee
    usd = btc * 50_000 * FX * 1.05 * 0.9995 / (FX * 1.01) - 25    # sold in Korea, converted, remitted
    assert r["real_var"] - r["real_fixed"] / size == pytest.approx(usd / size - 1, rel=1e-12)
    assert r["exp_var"] == pytest.approx(r["real_var"])          # flat prices: expected = realized


def test_usdt_path_hand_calculation(b):
    r = bt.usdt_returns(hourly(), b, fx_spread=0.005).iloc[0]
    size = 20_000
    usd = (size - 1.0) * FX * 1.05 * 0.9995 / (FX * 1.005) - 25
    assert r["real_var"] - r["real_fixed"] / size == pytest.approx(usd / size - 1, rel=1e-12)


def test_hedge_removes_price_risk_and_counts_only_funding_in_transit(b):
    h = hourly()
    h.loc[1:, ["coin_usdt", "coin_krw"]] *= 0.9                   # BTC falls 10% during the first transfer
    funding = pd.DataFrame({"funding_time": h["ts_utc"].iloc[[0, 1]], "rate": [0.01, 0.0003]})
    un = bt.btc_returns(h, funding, b, 0.01, hedged=False).iloc[0]
    he = bt.btc_returns(h, funding, b, 0.01, hedged=True).iloc[0]
    assert un["real_var"] < un["exp_var"] - 0.09                  # unhedged eats the fall
    q, f = 0.999, 0.0005
    # hedged: short P&L +10% of notional, futures fees on open and close, funding at t+1 only (not at t)
    expected_hedge = q * 0.1 - f * q * (1 + 0.9) + q * 0.0003
    assert he["real_var"] - un["real_var"] == pytest.approx(expected_hedge)
    assert he["exp_var"] == pytest.approx(un["exp_var"] - 2 * f * q)


def test_hedged_path_needs_funding_history(b):
    h = hourly(start="2019-09-09")
    funding = pd.DataFrame({"funding_time": pd.to_datetime(["2019-09-10 08:00"], utc=True), "rate": [0.0]})
    r = bt.btc_returns(h, funding, b, 0.01, hedged=True)
    assert r.loc[r["ts_utc"] < "2019-09-10 08:00", "real_var"].isna().all()


def frame(ts, exp, real):
    return pd.DataFrame({"ts_utc": ts, "exp_var": exp, "exp_fixed": 0.0, "real_var": real, "real_fixed": 0.0})


def test_cap_counts_proceeds_round_trip_and_new_year_reset():
    ts = pd.date_range("2024-12-20", "2025-01-10", freq="h", tz="UTC")
    r = frame(ts, 0.03, 0.02)
    r["exp_fixed"] = r["real_fixed"] = 25.0                       # $25 remittance fee
    t = bt.simulate(r, size=10_000, cap=25_000, entry_buffer=0.005, round_trip_days=2)
    y24, y25 = t[t["year"] == 2024], t[t["year"] == 2025]
    # proceeds of 10,175 + 10,175 leave 4,650 of cap; the last slice is sized on expected proceeds
    last = (25_000 - 2 * 10_175 + 25) / 1.03
    assert y24["amount_usd"].tolist() == pytest.approx([10_000, 10_000, last])
    assert (y24["ts_utc"].diff().dropna() >= pd.Timedelta(days=2)).all()
    assert y24["remitted_usd"].sum() == pytest.approx(2 * 10_175 + last * 1.02 - 25)
    assert y24["remitted_usd"].sum() < 25_000                    # landed worse than expected: under the cap
    assert y25["ts_utc"].iloc[0] == pd.Timestamp("2025-01-01 00:00", tz="UTC")  # capital was back by then
    assert len(y25) == 3                                          # then a slice too small to clear $25 is skipped
    assert t["profit_usd"].sum() == pytest.approx(2 * (2 * 175 + last * 0.02 - 25))


def test_entry_uses_expected_not_realized():
    ts = pd.date_range("2025-01-01", periods=10, freq="h", tz="UTC")
    exp = [0.001] * 10
    real = [0.001] * 10
    real[3] = 0.5                                                 # a windfall nobody could see at entry
    t = bt.simulate(frame(ts, exp, real), 10_000, 100_000, 0.005, 2)
    assert t.empty


def test_fixed_costs_can_block_a_small_last_slice():
    ts = pd.date_range("2025-01-01", periods=200, freq="h", tz="UTC")
    r = frame(ts, 0.02, 0.02)
    r["exp_fixed"] = r["real_fixed"] = 25.0                       # $25 remittance fee
    t = bt.simulate(r, size=10_000, cap=10_500, entry_buffer=0.005, round_trip_days=1)
    # proceeds 10,175 leave $325 of cap: a $343 slice, 2% - 25/343 = -5.3%, is below the buffer
    assert t["remitted_usd"].tolist() == pytest.approx([10_175])
    assert t["amount_usd"].tolist() == [10_000]


def test_hindsight_best_and_full_years():
    ts = pd.date_range("2024-06-01", "2026-03-01", freq="h", tz="UTC")
    real = np.where(ts == pd.Timestamp("2025-05-05 05:00", tz="UTC"), 0.08, 0.01)
    r = frame(ts, 0.0, real)
    best = bt.hindsight_best(r, 100_000)
    # proceeds A x 1.08 = 100,000 -> A = 92,593 and profit 7,407
    assert best[2025] == pytest.approx(100_000 * 0.08 / 1.08) and best[2024] == pytest.approx(100_000 * 0.01 / 1.01)
    assert bt.full_years(r) == [2025]


def test_a_trade_that_turns_out_badly_is_still_taken():
    """Entry must not peek at the outcome: a signal hour whose transfer loses money is still traded."""
    ts = pd.date_range("2025-01-01", periods=10, freq="h", tz="UTC")
    real = [0.02] * 10
    real[0] = -0.04                                               # the price moved against us in transit
    t = bt.simulate(frame(ts, [0.02] * 10, real), 10_000, 100_000, 0.005, 2)
    assert t["ts_utc"].iloc[0] == ts[0] and t["profit_usd"].iloc[0] == pytest.approx(-400)


def test_fresh_fx_filter_checks_both_entry_and_landing(b):
    h = hourly(n=6)
    h.loc[2, "fx_age_h"] = 30.0                                   # the fixing is stale at hour 2 only
    r = bt.require_fresh_fx(bt.usdt_returns(h, b, 0.01), max_age_hours=24)
    kept = r[bt.RET].notna().all(axis=1).tolist()                # rows the simulation can use
    # hour 1 lands at hour 2 (stale), hour 2 enters stale; the last hour has no landing price anyway
    assert kept == [True, False, False, True, True, False]
