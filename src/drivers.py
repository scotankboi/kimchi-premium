"""What moves with the premium. Associations only: no causal claim is made or tested.

Features, all observable at the daily sampling moment (no look-ahead):
    btc_ret      BTC/USDT return over the trailing window (Binance)
    turnover     ln(Upbit 24h KRW trading value / its trailing median): retail activity, detrended.
                 Needs a full baseline window, so it starts ~3 months after Upbit's 2017 launch,
                 when trading value was tiny and ratios reached several hundred.
    fx_vol       annualised stdev of daily USD/KRW log changes over the trailing fixings

The daily premium is highly persistent, so daily t-statistics would be badly overstated.
Monthly means are used for the regression, with Newey-West standard errors.
"""
import numpy as np
import pandas as pd

FEATURES = ["btc_ret", "turnover", "fx_vol"]


def features(daily: pd.DataFrame, fx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = cfg["drivers"]
    d = daily.set_index("date").sort_index()
    d = d.reindex(pd.date_range(d.index.min(), d.index.max(), freq="D"))   # date-based, never row-based, lags
    out = pd.DataFrame(index=d.index)
    out["kp"] = d["kp"]
    out["btc_ret"] = d["coin_usdt"] / d["coin_usdt"].shift(c["return_window_days"]) - 1
    base = d["upbit_value_24h_krw"].rolling(f"{c['turnover_baseline_days']}D",
                                            min_periods=c["turnover_baseline_days"]).median()
    out["turnover"] = np.log(d["upbit_value_24h_krw"] / base)
    f = fx.set_index("date")["usdkrw"].sort_index()
    vol = np.log(f).diff().rolling(c["fx_vol_fixings"]).std() * np.sqrt(252)
    # the fixing of a date is known at that date's sampling moment (both are NY noon)
    out["fx_vol"] = vol.reindex(out.index, method="ffill")
    return out.rename_axis("date").reset_index()


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Rank correlation without a scipy dependency (average ranks for ties)."""
    return a.rank().corr(b.rank())


def correlations(feat: pd.DataFrame, start: str) -> pd.DataFrame:
    f = feat[feat["date"] >= start]
    monthly = f.set_index("date").resample("MS").mean()
    rows = []
    for x in FEATURES:
        dly = f[["kp", x]].dropna()
        mth = monthly[["kp", x]].dropna()
        rows.append({"feature": x, "sample_start": start, "days": len(dly),
                     "spearman_daily": spearman(dly["kp"], dly[x]),
                     "months": len(mth), "spearman_monthly": spearman(mth["kp"], mth[x])})
    return pd.DataFrame(rows)


def quintiles(feat: pd.DataFrame, x: str, start: str) -> pd.DataFrame:
    f = feat[feat["date"] >= start][["kp", x]].dropna()
    f["bucket"] = pd.qcut(f[x], 5, labels=[f"Q{i}" for i in range(1, 6)])
    g = f.groupby("bucket", observed=True)
    return pd.DataFrame({"feature": x, "sample_start": start, "from": g[x].min(), "to": g[x].max(),
                         "days": g.size(), "median_kp": g["kp"].median(), "mean_kp": g["kp"].mean(),
                         "share_kp_above_3pct": g["kp"].apply(lambda s: (s > 0.03).mean())}).reset_index()


def newey_west_ols(y: np.ndarray, X: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS coefficients and Newey-West (Bartlett kernel) standard errors. X includes the constant."""
    n = len(y)
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    xu = X * u[:, None]
    s = xu.T @ xu
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)
        g = xu[lag:].T @ xu[:-lag]
        s += w * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv
    return beta, np.sqrt(np.diag(cov))


def monthly_regression(feat: pd.DataFrame, start: str, lags: int) -> pd.DataFrame:
    """Monthly mean KP (in %) on monthly mean features, each feature standardised (1 stdev)."""
    m = feat[feat["date"] >= start].set_index("date").resample("MS").mean()[["kp", *FEATURES]].dropna()
    z = (m[FEATURES] - m[FEATURES].mean()) / m[FEATURES].std()
    X = np.column_stack([np.ones(len(m)), z.to_numpy()])
    beta, se = newey_west_ols(m["kp"].to_numpy() * 100, X, lags)
    r2 = 1 - np.sum((m["kp"] * 100 - X @ beta) ** 2) / np.sum((m["kp"] * 100 - (m["kp"] * 100).mean()) ** 2)
    return pd.DataFrame({"term": ["const", *FEATURES], "coef_pp_per_1sd": beta, "nw_se": se, "t": beta / se,
                         "sample_start": start, "months": len(m), "r2": r2})
