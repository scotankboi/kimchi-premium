"""Decomposition series: Bithumb before Upbit listed USDT, Upbit after, and a check that the two agree.

Each segment uses one exchange for both legs (coin/KRW and USDT/KRW), so no
cross-exchange price gap leaks into the split between the two factors.
"""
import pandas as pd

from . import metrics

COMPONENTS = ["kp", "crypto_prem", "usdt_prem"]


def splice(upbit: pd.DataFrame, bithumb: pd.DataFrame) -> pd.DataFrame:
    """Daily decomposition: Bithumb until the first day Upbit has a USDT/KRW price, Upbit from then on."""
    switch = upbit.loc[upbit["usdt_krw"].notna(), "date"].min()
    before = bithumb[(bithumb["date"] < switch) & bithumb["usdt_krw"].notna()].assign(source="bithumb")
    after = upbit[upbit["date"] >= switch].assign(source="upbit")
    out = pd.concat([before, after], ignore_index=True)[["date", "source", *COMPONENTS]]
    return out.dropna(subset=["usdt_prem"]).reset_index(drop=True)


def overlap_stats(upbit: pd.DataFrame, bithumb: pd.DataFrame) -> pd.DataFrame:
    """Same dates, same sampling moment, two exchanges: how close are the three series?"""
    j = upbit[["date", *COMPONENTS]].merge(bithumb[["date", *COMPONENTS]], on="date", suffixes=("_upbit", "_bithumb"))
    j = j.dropna()
    rows = []
    for c in COMPONENTS:
        diff = (j[f"{c}_upbit"] - j[f"{c}_bithumb"]).abs()
        rows.append({"series": c, "days": len(j), "first": j["date"].min(), "last": j["date"].max(),
                     "mean_upbit": j[f"{c}_upbit"].mean(), "mean_bithumb": j[f"{c}_bithumb"].mean(),
                     "mean_abs_diff": diff.mean(), "p95_abs_diff": diff.quantile(0.95),
                     "correlation": j[f"{c}_upbit"].corr(j[f"{c}_bithumb"])})
    return pd.DataFrame(rows)


def contribution_table(spliced: pd.DataFrame, large_premium: float) -> pd.DataFrame:
    """Contribution by exchange segment, by year, overall, and on large-premium days only."""
    groups = [(f"{s} segment", g) for s, g in spliced.groupby("source", sort=False)]
    groups += [(str(y), g) for y, g in spliced.groupby(spliced["date"].dt.year)]
    groups += [("All", spliced), (f"All, days with KP > {large_premium:.0%}", spliced[spliced["kp"] > large_premium])]
    rows = []
    for label, g in groups:
        rows.append({"period": label, "first": g["date"].min().date(), "last": g["date"].max().date(),
                     **metrics.contribution(g["kp"], g["crypto_prem"], g["usdt_prem"])})
    return pd.DataFrame(rows).set_index("period")
