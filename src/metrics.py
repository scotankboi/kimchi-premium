"""Premium definitions. Pure functions, no I/O, so they can be unit-tested by hand.

    KP            = BTC/KRW (Upbit) / (BTC/USDT (Binance) * USD/KRW (FX)) - 1
    crypto_prem   = BTC/KRW (Upbit) / (BTC/USDT (Binance) * USDT/KRW (Upbit)) - 1
    usdt_prem     = USDT/KRW (Upbit) / USD/KRW (FX) - 1

    (1 + KP) = (1 + crypto_prem) * (1 + usdt_prem)   exactly, by construction.

crypto_prem uses only exchange prices observed at the same moment, so it
carries no FX timing error. usdt_prem carries all of it.
"""
import numpy as np
import pandas as pd


def kimchi_premium(coin_krw, coin_usdt, usdkrw):
    return coin_krw / (coin_usdt * usdkrw) - 1


def decompose(coin_krw, coin_usdt, usdt_krw, usdkrw) -> pd.DataFrame:
    """KP and its two factors. usdt_krw may be NaN (before the KRW-USDT listing)."""
    return pd.DataFrame({
        "kp": kimchi_premium(coin_krw, coin_usdt, usdkrw),
        "crypto_prem": coin_krw / (coin_usdt * usdt_krw) - 1,
        "usdt_prem": usdt_krw / usdkrw - 1,
    })


def summary_by_year(premium: pd.Series) -> pd.DataFrame:
    """Distribution of a daily premium series (index = dates) per calendar year and overall."""
    s = premium.dropna()
    groups = [(str(y), g) for y, g in s.groupby(s.index.year)] + [("All", s)]
    rows = []
    for label, g in groups:
        rows.append({
            "period": label,
            "days": len(g),
            "first": g.index.min().date(),
            "last": g.index.max().date(),
            "mean": g.mean(),
            "median": g.median(),
            "p05": g.quantile(0.05),
            "p95": g.quantile(0.95),
            "min": g.min(),
            "max": g.max(),
            "share_negative": float((g < 0).mean()),
        })
    return pd.DataFrame(rows).set_index("period")



def log_components(kp, crypto_prem, usdt_prem) -> pd.DataFrame:
    """ln(1+KP) = ln(1+crypto) + ln(1+usdt): the additive form of the decomposition."""
    return pd.DataFrame({"log_kp": np.log1p(kp), "log_crypto": np.log1p(crypto_prem),
                         "log_usdt": np.log1p(usdt_prem)})


def contribution(kp, crypto_prem, usdt_prem) -> dict:
    """Which factor makes up the premium, two ways (log terms, so the parts add up exactly):

    level share     mean(ln(1+usdt)) / mean(ln(1+KP)): share of the average premium.
                    Unstable when the average premium is near zero, so read it with mean_kp.
    variance share  cov(ln(1+usdt), ln(1+KP)) / var(ln(1+KP)): share of the premium's day-to-day
                    variation; the USDT and crypto shares sum to 1.
    """
    l = log_components(kp, crypto_prem, usdt_prem).dropna()
    out = {"days": len(l), "mean_kp": np.expm1(l["log_kp"].mean()),
           "mean_usdt_prem": np.expm1(l["log_usdt"].mean()), "mean_crypto_prem": np.expm1(l["log_crypto"].mean()),
           "usdt_level_share": np.nan, "usdt_variance_share": np.nan, "crypto_variance_share": np.nan}
    if len(l) < 2:
        return out
    if l["log_kp"].mean() != 0:
        out["usdt_level_share"] = l["log_usdt"].mean() / l["log_kp"].mean()
    var = l["log_kp"].var()
    if var > 1e-18:  # a constant premium leaves float residue (~1e-32), not variation worth splitting
        out["usdt_variance_share"] = l["log_usdt"].cov(l["log_kp"]) / var
        out["crypto_variance_share"] = l["log_crypto"].cov(l["log_kp"]) / var
    return out
