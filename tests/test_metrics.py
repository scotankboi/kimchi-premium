import math

import numpy as np
import pandas as pd
import pytest

from src import metrics


def test_premium_hand_calculation():
    # 100,000,000 KRW / (70,000 USDT * 1,400 KRW/USD) = 100.0m / 98.0m
    assert metrics.kimchi_premium(100_000_000, 70_000, 1_400) == pytest.approx(100 / 98 - 1)


def test_decomposition_hand_calculation_and_identity():
    out = metrics.decompose(pd.Series([100_000_000.0]), pd.Series([70_000.0]),
                            pd.Series([1_428.0]), pd.Series([1_400.0])).iloc[0]
    assert out["usdt_prem"] == pytest.approx(0.02)                        # 1428 / 1400 - 1
    assert out["crypto_prem"] == pytest.approx(100e6 / (70_000 * 1_428) - 1)
    assert 1 + out["kp"] == pytest.approx((1 + out["crypto_prem"]) * (1 + out["usdt_prem"]), rel=1e-15)


def test_decomposition_without_usdt_market_keeps_kp_only():
    out = metrics.decompose(pd.Series([100.0]), pd.Series([1.0]), pd.Series([np.nan]), pd.Series([90.0])).iloc[0]
    assert out["kp"] == pytest.approx(100 / 90 - 1)
    assert math.isnan(out["crypto_prem"]) and math.isnan(out["usdt_prem"])


def test_summary_by_year():
    idx = pd.to_datetime(["2020-01-01", "2020-06-01", "2020-12-31", "2021-03-01"])
    s = pd.Series([0.01, 0.03, -0.01, np.nan], index=idx)
    t = metrics.summary_by_year(s)
    assert list(t.index) == ["2020", "All"]  # the NaN-only year disappears instead of showing zeros
    assert t.loc["2020", "days"] == 3
    assert t.loc["2020", "mean"] == pytest.approx(0.01)
    assert t.loc["2020", "median"] == pytest.approx(0.01)
    assert t.loc["2020", "share_negative"] == pytest.approx(1 / 3)
    assert t.loc["All", "max"] == pytest.approx(0.03)


def test_contribution_when_crypto_premium_is_zero():
    usdt = pd.Series([0.01, 0.05, 0.02, -0.01])
    c = metrics.contribution(usdt, pd.Series(0.0, index=usdt.index), usdt)
    assert c["usdt_level_share"] == pytest.approx(1) and c["usdt_variance_share"] == pytest.approx(1)
    assert c["crypto_variance_share"] == pytest.approx(0)


def test_contribution_shares_add_up_and_constant_kp_gives_nan():
    rng = np.random.default_rng(0)
    u, cr = pd.Series(rng.normal(0.02, 0.02, 500)), pd.Series(rng.normal(0, 0.005, 500))
    kp = (1 + u) * (1 + cr) - 1
    c = metrics.contribution(kp, cr, u)
    assert c["usdt_variance_share"] + c["crypto_variance_share"] == pytest.approx(1)
    # ln(1+u) = [.02, .04], ln(1+c) = [.01, -.01] -> ln(1+KP) = [.03, .03]: level share 1, no variance to split
    u2, c2 = pd.Series(np.expm1([0.02, 0.04])), pd.Series(np.expm1([0.01, -0.01]))
    c = metrics.contribution((1 + u2) * (1 + c2) - 1, c2, u2)
    assert c["usdt_level_share"] == pytest.approx(1) and np.isnan(c["usdt_variance_share"])
