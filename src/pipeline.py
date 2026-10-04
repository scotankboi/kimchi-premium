"""End-to-end run: fetch -> align -> quality report -> summary table -> figures.

    python -m src.pipeline            # use cached raw data when it matches config.yaml
    python -m src.pipeline --refresh  # re-download everything (to extend to today)
"""
import argparse
import json
import logging
from datetime import datetime, timezone

import pandas as pd

from . import backtest, candles, decomposition, drivers, events, metrics, plots, quality
from .build_dataset import build_daily, build_hourly
from .config import ROOT, load_config
from .fetch_binance import fetch_funding, fetch_klines
from .fetch_fx import fetch_fred
from .fetch_krw import fetch_candles
from .net import HttpClient

log = logging.getLogger("pipeline")
RAW, OUT, FIG = ROOT / "data" / "raw", ROOT / "data" / "processed", ROOT / "figures"
MANIFEST = RAW / "manifest.json"


def _cached(name: str, params: dict, fetch, loader, saver, refresh: bool):
    """Reuse a raw file only if it was fetched with exactly these parameters."""
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    path = RAW / name
    if not refresh and path.exists() and manifest.get(name, {}).get("params") == params:
        log.info("cached %s (fetched %s)", name, manifest[name]["fetched_at"])
        return loader(path)
    df = fetch()
    saver(df, path)
    manifest[name] = {"params": params, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "rows": len(df)}
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    return df


def _save_csv(df: pd.DataFrame, path) -> None:
    df.to_csv(path, index=False)


def _load_fx(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.as_unit("ns")
    return df


def _load_funding(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["funding_time"] = pd.to_datetime(df["funding_time"], utc=True).dt.as_unit("ns")
    return df


def collect(cfg: dict, refresh: bool) -> dict[str, pd.DataFrame]:
    RAW.mkdir(parents=True, exist_ok=True)
    start, end = cfg["period"]["start"], cfg["period"]["end"]
    upbit = HttpClient(cfg["upbit"]["sleep_sec"])
    binance = HttpClient(cfg["binance"]["sleep_sec"])
    m = cfg["markets"]
    data = {}
    for key, market, interval in (("coin_h", m["upbit_coin"], "1h"), ("coin_d", m["upbit_coin"], "1d"),
                                  ("usdt_h", m["upbit_usdt"], "1h")):
        params = {"source": "upbit", "base_url": cfg["upbit"]["base_url"], "market": market,
                  "interval": interval, "start": start, "end": end}
        data[key] = _cached(f"upbit_{market}_{interval}.csv", params,
                            lambda: fetch_candles(upbit, cfg["upbit"]["base_url"], market, interval, start, end),
                            candles.load, candles.save, refresh)
    bstart = cfg["bithumb"]["start"]
    bithumb = HttpClient(cfg["bithumb"]["sleep_sec"])
    for key, market in (("bithumb_coin_h", m["bithumb_coin"]), ("bithumb_usdt_h", m["bithumb_usdt"])):
        params = {"source": "bithumb", "base_url": cfg["bithumb"]["base_url"], "market": market,
                  "interval": "1h", "start": bstart, "end": end}
        data[key] = _cached(f"bithumb_{market}_1h.csv", params,
                            lambda: fetch_candles(bithumb, cfg["bithumb"]["base_url"], market, "1h", bstart, end,
                                                  exchange="bithumb"),
                            candles.load, candles.save, refresh)
    for key, interval in (("binance_h", "1h"), ("binance_d", "1d")):
        params = {"source": "binance", "symbol": m["binance_coin"], "interval": interval, "start": start, "end": end}
        data[key] = _cached(f"binance_{m['binance_coin']}_{interval}.csv", params,
                            lambda: fetch_klines(binance, cfg["binance"]["base_urls"], m["binance_coin"],
                                                 interval, start, end),
                            candles.load, candles.save, refresh)
    params = {"source": "binance_funding", "base_url": cfg["binance"]["futures_base_url"], "symbol": m["binance_coin"],
              "start": cfg["binance"]["funding_start"], "end": end}
    data["funding"] = _cached(f"binance_funding_{m['binance_coin']}.csv", params,
                              lambda: fetch_funding(binance, cfg["binance"]["futures_base_url"], m["binance_coin"],
                                                    cfg["binance"]["funding_start"], end),
                              _load_funding, _save_csv, refresh)
    params = {"source": "fred", "url": cfg["fx"]["url"], "start": start}
    data["fx"] = _cached("fx_usdkrw.csv", params, lambda: fetch_fred(upbit, cfg["fx"]["url"], start),
                         _load_fx, _save_csv, refresh)
    return data


# Hourly close-up windows for chart 3, keyed by the event label in config.yaml
ZOOMS = {
    "Exchange-ban remarks": ("2018-01-09 00:00", "2018-01-13 00:00"),
    "Tesla BTC purchase": ("2021-02-07 00:00", "2021-02-11 00:00"),
    "Martial law declared": ("2024-12-03 06:00", "2024-12-04 12:00"),
    "Crypto liquidation cascade": ("2025-10-09 00:00", "2025-10-14 00:00"),
}


def run(refresh: bool = False) -> dict:
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    d = collect(cfg, refresh)

    daily = build_daily(d["coin_h"], d["usdt_h"], d["binance_h"], d["fx"], cfg)
    hourly = build_hourly(d["coin_h"], d["usdt_h"], d["binance_h"], d["fx"], cfg)
    bithumb = build_daily(d["bithumb_coin_h"], d["bithumb_usdt_h"], d["binance_h"], d["fx"], cfg)
    daily.to_csv(OUT / "premium_daily.csv", index=False)
    hourly.to_csv(OUT / "premium_hourly.csv.gz", index=False)
    bithumb.to_csv(OUT / "premium_daily_bithumb.csv", index=False)

    max_ret = cfg["quality"]["max_abs_hourly_return"]
    report = {
        "raw": {
            "upbit_coin_1h": quality.candle_report(d["coin_h"], "1h", max_ret),
            "upbit_usdt_1h": quality.candle_report(d["usdt_h"], "1h", max_ret),
            "binance_coin_1h": quality.candle_report(d["binance_h"], "1h", max_ret),
            "bithumb_coin_1h": quality.candle_report(d["bithumb_coin_h"], "1h", max_ret),
            "bithumb_usdt_1h": quality.candle_report(d["bithumb_usdt_h"], "1h", max_ret),
        },
        "daily_close_equals_last_hourly_close": {
            "upbit": quality.daily_vs_hourly(d["coin_d"], d["coin_h"]),
            "binance": quality.daily_vs_hourly(d["binance_d"], d["binance_h"]),
        },
        "bithumb_vs_upbit_same_hour": {
            "coin": quality.cross_exchange_alignment(d["coin_h"], d["bithumb_coin_h"]),
            "usdt": quality.cross_exchange_alignment(d["usdt_h"], d["bithumb_usdt_h"]),
        },
        **quality.dataset_report(daily, hourly, cfg),
    }
    (OUT / "quality_report.json").write_text(json.dumps(report, indent=2, default=str))

    summary = metrics.summary_by_year(daily.set_index("date")["kp"])
    summary.to_csv(OUT / "summary_by_year.csv", float_format="%.5f")

    # decomposition, drivers, events
    spliced = decomposition.splice(daily, bithumb)
    spliced.to_csv(OUT / "decomposition_daily.csv", index=False)
    decomposition.overlap_stats(daily, bithumb).to_csv(OUT / "decomposition_overlap_check.csv", index=False,
                                                       float_format="%.6f")
    decomposition.contribution_table(spliced, cfg["decomposition"]["large_premium"]).to_csv(
        OUT / "decomposition_contribution.csv", float_format="%.5f")

    feat = drivers.features(daily, d["fx"], cfg)
    starts, lags = cfg["drivers"]["sample_starts"], cfg["drivers"]["newey_west_lags"]
    pd.concat([drivers.correlations(feat, s) for s in starts]).to_csv(
        OUT / "drivers_correlations.csv", index=False, float_format="%.4f")
    quint = {s: pd.concat([drivers.quintiles(feat, x, s) for x in drivers.FEATURES]) for s in starts}
    pd.concat(quint.values()).to_csv(OUT / "drivers_quintiles.csv", index=False, float_format="%.5f")
    pd.concat([drivers.monthly_regression(feat, s, lags) for s in starts]).to_csv(
        OUT / "drivers_monthly_regression.csv", index=False, float_format="%.4f")

    ev = events.event_check(hourly, cfg, d["usdt_h"])
    ev.to_csv(OUT / "events_check.csv", index=False, float_format="%.5f")

    plots.premium_timeseries(daily, cfg, FIG / "02_premium_timeseries.png", ev)
    plots.decomposition(spliced, FIG / "01_decomposition.png")
    plots.overlap_check(daily, bithumb, FIG / "01b_upbit_vs_bithumb.png")
    panels = []
    for r in ev[ev["annotate"] & ev["label"].isin(ZOOMS)].itertuples():
        start, end = ZOOMS[r.label]
        panels.append({"title": f"{r.time_utc:%Y-%m-%d} {r.label}", "start": start, "end": end,
                       "mark": r.time_utc.tz_convert(None),
                       # hourly KP is only as good as the FX fixing behind it, so show its age at both extremes
                       "note": f"FX fixing age: {r.fx_age_h_at_kp_low:.0f}h at KP low, "
                               f"{r.fx_age_h_at_kp_high:.0f}h at KP high"})
    plots.event_zooms(hourly, panels, FIG / "03_event_closeups.png")
    plots.return_quintiles([quint[s][quint[s]["feature"] == "btc_ret"] for s in starts],
                           FIG / "04_premium_by_btc_return.png")
    run_backtest(hourly, d["funding"], cfg)
    log.info("done: %d daily rows, %d hourly rows", len(daily), len(hourly))
    return report


def run_backtest(hourly: pd.DataFrame, funding: pd.DataFrame, cfg: dict) -> None:
    b = cfg["backtest"]
    fresh_h = b["fresh_fx_max_age_hours"]
    variants = {"all hours": lambda r: r,
                f"fresh FX only (fixing <= {fresh_h:g}h old)": lambda r: backtest.require_fresh_fx(r, fresh_h)}

    def builders(bt_cfg):
        return {
            "btc_unhedged": lambda sp: backtest.btc_returns(hourly, funding, bt_cfg, sp, hedged=False),
            "btc_hedged": lambda sp: backtest.btc_returns(hourly, funding, bt_cfg, sp, hedged=True),
            "usdt": lambda sp: backtest.usdt_returns(hourly, bt_cfg, sp),
        }
    base = {k: f(b["fx_spread"]) for k, f in builders(b).items()}
    common_start = base["usdt"].dropna(subset=backtest.RET)["ts_utc"].min()   # first hour all paths exist
    hedge_start = base["btc_hedged"].dropna(subset=backtest.RET)["ts_utc"].min()

    yearly, trades, risk = [], [], []
    for vname, vf in variants.items():
        for path, r0 in base.items():
            r = vf(r0)
            yearly.append(backtest.yearly(r, b, b["entry_buffer"]).assign(path=path, fx_filter=vname))
            trades.append(backtest.simulate(r, b["trade_size_usd"], b["annual_remit_cap_usd"], b["entry_buffer"],
                                            b["round_trip_days"]).assign(path=path, fx_filter=vname))
            for window, start in (("since USDT listing", common_start), ("since perpetual launch", hedge_start)):
                if path == "usdt" and window != "since USDT listing":
                    continue
                risk.append({"path": path, "fx_filter": vname, "window": window, "from": start,
                             **backtest.execution_risk(r[r["ts_utc"] >= start], b["trade_size_usd"], b["entry_buffer"])})
    yearly = pd.concat(yearly, ignore_index=True)
    yearly.to_csv(OUT / "backtest_yearly.csv", index=False, float_format="%.4f")
    pd.concat(trades, ignore_index=True).to_csv(OUT / "backtest_trades.csv", index=False, float_format="%.6f")
    pd.DataFrame(risk).to_csv(OUT / "backtest_execution_risk.csv", index=False, float_format="%.6f")

    # robustness summary over full calendar years: stale FX filtered out, fixed fees doubled
    doubled = {**b, "fees": {**b["fees"], **{k: 2 * b["fees"][k] for k in
                                            ("btc_withdrawal_btc", "usdt_withdrawal_usdt", "remit_fee_usd")}}}
    rows = []
    for label, bt_cfg, vf in (("baseline", b, variants["all hours"]),
                              (f"fresh FX only (fixing <= {fresh_h:g}h old)", b, list(variants.values())[1]),
                              ("fixed fees doubled", doubled, variants["all hours"])):
        for path, build in builders(bt_cfg).items():
            r = vf(build(bt_cfg["fx_spread"]))
            years = backtest.full_years(base[path])
            y = backtest.yearly(r, bt_cfg, bt_cfg["entry_buffer"]).set_index("year").reindex(years)
            rows.append({"variant": label, "path": path, "full_years": f"{years[0]}-{years[-1]}" if len(years) > 1
                         else str(years[0]), "median_rule_profit_usd": y["profit_usd"].median(),
                         "min_hindsight_usd": y["hindsight_best_usd"].min(),
                         "max_hindsight_usd": y["hindsight_best_usd"].max(),
                         "min_no_cap_usd": y["profit_without_cap_usd"].min(),
                         "max_no_cap_usd": y["profit_without_cap_usd"].max()})
    pd.DataFrame(rows).to_csv(OUT / "backtest_robustness.csv", index=False, float_format="%.0f")

    # Headline basis: hedged BTC and USDT paths (no luck from price moves in transit), fresh FX only
    # (no premia created by a stale fixing). The other variants stay in the CSVs.
    fresh_name, fresh_fn = list(variants.items())[1]
    sens, sens_trades = [], []
    for p in ("btc_hedged", "usdt"):
        log_p: list = []
        sens.append(backtest.sensitivity(builders(b)[p], b, fresh_fn, log_p).assign(path=p))
        sens_trades += [t.assign(path=p) for t in log_p]
    sens = pd.concat(sens, ignore_index=True)
    sens.to_csv(OUT / "backtest_sensitivity.csv", index=False, float_format="%.4f")
    pd.concat(sens_trades, ignore_index=True).to_csv(OUT / "backtest_sensitivity_trades.csv", index=False,
                                                     float_format="%.6f")
    main = yearly[(yearly["path"] == "btc_hedged") & (yearly["fx_filter"] == fresh_name)]
    plots.backtest_by_year(main, b, FIG / "05_backtest_by_year.png", "BTC path hedged with the perpetual", fresh_name,
                           backtest.full_years(base["btc_hedged"]))
    plots.sensitivity_heatmap(sens, b, FIG / "06_backtest_sensitivity.png", fresh_name)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download raw data even if cached")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(refresh=args.refresh)


if __name__ == "__main__":
    main()
