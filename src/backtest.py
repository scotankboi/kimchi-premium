"""Capacity-constrained arbitrage backtest, positive-premium direction only.

Paths (every round trip ends by converting won to dollars and remitting them abroad; the
dollars remitted, i.e. the trade's proceeds, count against the annual undocumented-remittance cap):

    BTC path   USD -> buy BTC on Binance -> withdraw to Upbit (transfer_hours) -> sell for KRW
               -> KRW to USD at the bank (fx_spread) -> remit (fixed fee)
               unhedged: carries BTC price risk while in transit
               hedged:   short the BTC perpetual on Binance for the transit; pays futures fees,
                         receives (or pays) any funding settled in transit. The spot price is used
                         as a proxy for the perpetual price (basis ignored).
    USDT path  USD = USDT on Binance -> withdraw to Upbit -> sell USDT for KRW -> KRW to USD -> remit
               only exists after Upbit listed USDT (2024-06-07)

Returns are split into a per-dollar part and a fixed cost in USD, so a trade of any size is
r(size) = var - fixed_usd / size. Entry uses only prices observable at the entry hour
("expected"); the outcome uses prices when the transfer lands ("realized").

Won are converted to dollars at the latest FRED DEXKOUS fixing at the landing hour times
(1 + fx_spread). That fixing can be a day old, or several days over a weekend, so hours when
the won moved sharply can show a premium that was not really there. `require_fresh_fx`
drops every hour whose fixing at entry or at landing is older than a limit; the pipeline
reports both versions.
"""
import numpy as np
import pandas as pd

HOUR = pd.Timedelta(hours=1)
RET = ["exp_var", "exp_fixed", "real_var", "real_fixed"]


def _later(h: pd.DataFrame, col: str, hours: int) -> pd.Series:
    """Value `hours` later on the complete hourly grid (NaN if missing)."""
    s = h.set_index("ts_utc")[col]
    return s.reindex(s.index + hours * HOUR).to_numpy()


def btc_returns(h: pd.DataFrame, funding: pd.DataFrame, bt: dict, fx_spread: float, hedged: bool) -> pd.DataFrame:
    f, T = bt["fees"], bt["transfer_hours"]["btc"]
    p0, k0, fx0 = h["coin_usdt"].to_numpy(), h["coin_krw"].to_numpy(), h["usdkrw"].to_numpy()
    p1, k1, fx1 = _later(h, "coin_usdt", T), _later(h, "coin_krw", T), _later(h, "usdkrw", T)
    keep = (1 - f["binance_spot_taker"]) * (1 - f["upbit_trade"]) / (1 + fx_spread)

    def leg(krw, fx):
        var = keep * krw / (p0 * fx) - 1
        fixed = f["btc_withdrawal_btc"] * krw * (1 - f["upbit_trade"]) / (fx * (1 + fx_spread)) + f["remit_fee_usd"]
        return var, fixed

    exp_var, exp_fixed = leg(k0, fx0)
    real_var, real_fixed = leg(k1, fx1)
    if hedged:
        qty = 1 - f["binance_spot_taker"]                          # BTC notional per dollar, in entry-price units
        fut_fee = f["binance_futures_taker"] * qty
        exp_var = exp_var - 2 * fut_fee                           # open and close the short
        funding_in_transit = _funding_between(h["ts_utc"], T, funding)
        real_var = real_var + qty * (p0 - p1) / p0 - fut_fee * (1 + p1 / p0) + qty * funding_in_transit
    return _frame(h, T, exp_var, exp_fixed, real_var, real_fixed)


def _frame(h: pd.DataFrame, T: int, exp_var, exp_fixed, real_var, real_fixed) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": h["ts_utc"], "exp_var": exp_var, "exp_fixed": exp_fixed,
                         "real_var": real_var, "real_fixed": real_fixed,
                         "fx_age_entry": h["fx_age_h"].to_numpy(), "fx_age_exit": _later(h, "fx_age_h", T)})


def require_fresh_fx(r: pd.DataFrame, max_age_hours: float) -> pd.DataFrame:
    """Blank out hours whose FX fixing at entry or at landing is older than the limit."""
    out = r.copy()
    stale = (out["fx_age_entry"] > max_age_hours) | (out["fx_age_exit"] > max_age_hours)
    out.loc[stale, ["exp_var", "real_var"]] = np.nan
    return out


def _funding_between(ts: pd.Series, hours: int, funding: pd.DataFrame) -> np.ndarray:
    """Sum of funding rates settled in (t, t+hours]; a short receives a positive rate.
    NaN before the perpetual existed, so the hedged variant starts when funding data starts."""
    if hours == 0:
        return np.zeros(len(ts))
    rate = funding.set_index("funding_time")["rate"]
    grid = pd.Series(0.0, index=pd.date_range(ts.min(), ts.max() + hours * HOUR, freq="h"))
    grid = grid.add(rate.reindex(grid.index), fill_value=0.0)
    window = grid.rolling(hours).sum().shift(-hours)               # (t, t+hours]
    out = window.reindex(ts).to_numpy(copy=True)          # pandas 3 returns read-only views
    out[(ts < funding["funding_time"].min()).to_numpy()] = np.nan
    return out


def usdt_returns(h: pd.DataFrame, bt: dict, fx_spread: float) -> pd.DataFrame:
    f, T = bt["fees"], bt["transfer_hours"]["usdt"]
    u0, fx0 = h["usdt_krw"].to_numpy(), h["usdkrw"].to_numpy()
    u1, fx1 = _later(h, "usdt_krw", T), _later(h, "usdkrw", T)
    keep = (1 - f["upbit_trade"]) / (1 + fx_spread)

    def leg(u, fx):
        var = keep * u / fx - 1
        fixed = f["usdt_withdrawal_usdt"] * keep * u / fx + f["remit_fee_usd"]
        return var, fixed

    exp_var, exp_fixed = leg(u0, fx0)
    real_var, real_fixed = leg(u1, fx1)
    return _frame(h, T, exp_var, exp_fixed, real_var, real_fixed)


def simulate(r: pd.DataFrame, size: float, cap: float | None, entry_buffer: float, round_trip_days: float) -> pd.DataFrame:
    """One pool of `size` dollars. Trade at the first hour whose expected net return exceeds the buffer,
    wait for the capital to come back, repeat. cap=None removes the cap (to measure what it costs).

    The cap applies to the dollars remitted out of Korea, which are the trade's proceeds
    (amount x (1 + realized)), and resets every 1 January. On the hedged path the futures P&L
    stays offshore but is counted here too; over a one-hour transfer the difference is small.
    Each slice is sized so that its proceeds *expected at entry* fit in what is left of the cap;
    if the transfer lands better than expected, the year can end slightly over the cap (visible
    in `remitted_usd`)."""
    r = r.dropna(subset=RET).sort_values("ts_utc")
    signal = r[r["exp_var"] - r["exp_fixed"] / size > entry_buffer]
    trades, year, used, free_at = [], None, 0.0, None
    for row in signal.itertuples():
        if row.ts_utc.year != year:
            year, used = row.ts_utc.year, 0.0
        if free_at is not None and row.ts_utc < free_at:
            continue
        amount = size if cap is None else min(size, (cap - used + row.exp_fixed) / (1 + row.exp_var))
        if amount <= 0:
            continue
        expected = row.exp_var - row.exp_fixed / amount
        if expected <= entry_buffer:                               # a small last slice may not clear fixed costs
            continue
        realized = row.real_var - row.real_fixed / amount
        proceeds = amount * (1 + realized)
        trades.append({"ts_utc": row.ts_utc, "year": year, "amount_usd": amount, "remitted_usd": proceeds,
                       "expected": expected, "realized": realized, "profit_usd": proceeds - amount})
        used += proceeds
        free_at = row.ts_utc + pd.Timedelta(days=round_trip_days)
    return pd.DataFrame(trades, columns=["ts_utc", "year", "amount_usd", "remitted_usd", "expected", "realized",
                                         "profit_usd"])


def hindsight_best(r: pd.DataFrame, cap: float) -> pd.Series:
    """Upper bound per year: one trade on the single best hour, known in advance, sized so that its
    proceeds use the whole cap. Proceeds A(1 + v) - fixed = cap gives A = (cap + fixed) / (1 + v),
    and profit = cap - A."""
    r = r.dropna(subset=RET)
    amount = (cap + r["real_fixed"]) / (1 + r["real_var"])
    best = (cap - amount).groupby(r["ts_utc"].dt.year).max()
    return best.clip(lower=0).rename("hindsight_best_usd")


def yearly(r: pd.DataFrame, bt: dict, entry_buffer: float) -> pd.DataFrame:
    size, cap, rt = bt["trade_size_usd"], bt["annual_remit_cap_usd"], bt["round_trip_days"]
    capped = simulate(r, size, cap, entry_buffer, rt)
    free = simulate(r, size, None, entry_buffer, rt)
    valid = r.dropna(subset=RET)
    hours = valid.groupby(valid["ts_utc"].dt.year).size().rename("hours_with_data")
    sig = (valid["exp_var"] - valid["exp_fixed"] / size > entry_buffer).groupby(valid["ts_utc"].dt.year).mean()
    g = capped.groupby("year")
    out = pd.concat([
        hours, sig.rename("share_hours_signalling"),
        g.size().rename("trades"), g["remitted_usd"].sum().rename("remitted_usd"),
        g["profit_usd"].sum().rename("profit_usd"), g["realized"].mean().rename("mean_realized"),
        free.groupby("year")["profit_usd"].sum().rename("profit_without_cap_usd"),
        hindsight_best(r, cap),
    ], axis=1)
    out.index.name = "year"
    for c in ("trades", "remitted_usd", "profit_usd", "profit_without_cap_usd"):
        out[c] = out[c].fillna(0)
    return out.reset_index()


def execution_risk(r: pd.DataFrame, size: float, entry_buffer: float) -> dict:
    """Over every signalling hour: how far the landed result is from what was expected at entry."""
    v = r.dropna(subset=RET)
    v = v[v["exp_var"] - v["exp_fixed"] / size > entry_buffer]
    exp = v["exp_var"] - v["exp_fixed"] / size
    real = v["real_var"] - v["real_fixed"] / size
    slip = real - exp
    return {"signal_hours": len(v), "mean_expected": exp.mean(), "mean_realized": real.mean(),
            "slippage_std": slip.std(), "slippage_p05": slip.quantile(0.05), "share_realized_negative": (real < 0).mean()}


def full_years(r: pd.DataFrame) -> list[int]:
    """Calendar years the return series covers from 1 January to 31 December."""
    v = r.dropna(subset=RET)
    first, last = v["ts_utc"].min(), v["ts_utc"].max()
    return [y for y in range(first.year, last.year + 1)
            if first <= pd.Timestamp(f"{y}-01-01 01:00", tz="UTC") and last >= pd.Timestamp(f"{y}-12-31 23:00", tz="UTC")]


def sensitivity(build, bt: dict, transform=lambda r: r, trades_out: list | None = None) -> pd.DataFrame:
    """Annual profit under the cap for each (fx_spread, entry_buffer), over full calendar years only.
    `build(fx_spread)` returns the path's return frame; `transform` applies e.g. the fresh-FX filter.
    If `trades_out` is a list, each cell's trade log is appended to it (to explain the grid)."""
    rows = []
    for spread in bt["sensitivity"]["fx_spreads"]:
        raw = build(spread)
        years = full_years(raw)                                     # calendar coverage of the path itself
        r = transform(raw)
        for buffer in bt["sensitivity"]["entry_buffers"]:
            t = simulate(r, bt["trade_size_usd"], bt["annual_remit_cap_usd"], buffer, bt["round_trip_days"])
            if trades_out is not None:
                trades_out.append(t.assign(fx_spread=spread, entry_buffer=buffer))
            annual = t.groupby("year")["profit_usd"].sum().reindex(years, fill_value=0.0)
            label = "" if not years else str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"
            rows.append({"fx_spread": spread, "entry_buffer": buffer, "years": label,
                         "n_years": len(years), "median_annual_profit_usd": annual.median(),
                         "mean_annual_profit_usd": annual.mean(), "min_annual_profit_usd": annual.min()})
    return pd.DataFrame(rows)
