"""Load config.yaml and reject anything the code does not use.

A key that no calculation reads is worse than a missing one: it looks like an
assumption was applied when it was not. So unknown keys raise.
"""
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = {
    "period": {"start": str, "end": (str, type(None))},
    "markets": {"upbit_coin": str, "upbit_usdt": str, "binance_coin": str,
                "bithumb_coin": str, "bithumb_usdt": str},
    "bithumb": {"base_url": str, "sleep_sec": (int, float), "start": str},
    "upbit": {"base_url": str, "sleep_sec": (int, float)},
    "binance": {"base_urls": list, "sleep_sec": (int, float), "futures_base_url": str, "funding_start": str},
    "fx": {"url": str, "fixing_time": str, "fixing_tz": str, "max_age_days": int},
    "sampling": {"price_tolerance_hours": int},
    "decomposition": {"large_premium": (int, float)},
    "drivers": {"return_window_days": int, "turnover_baseline_days": int, "fx_vol_fixings": int,
                "newey_west_lags": int, "sample_starts": list},
    "events": {"min_move_pp": (int, float), "hours_before": int, "hours_after": int,
               "turnover_baseline_days": int, "list": list},
    "backtest": {"trade_size_usd": (int, float), "annual_remit_cap_usd": (int, float), "round_trip_days": (int, float),
                 "entry_buffer": (int, float), "fx_spread": (int, float), "transfer_hours": dict, "fees": dict,
                 "sensitivity": dict, "fresh_fx_max_age_hours": (int, float)},
    "quality": {
        "max_abs_hourly_return": (int, float),
        "max_abs_premium": (int, float),
        "max_abs_usdt_premium": (int, float),
    },
}


def validate(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be a mapping")
    unknown = set(cfg) - set(SCHEMA)
    missing = set(SCHEMA) - set(cfg)
    if unknown or missing:
        raise ValueError(f"config sections: unknown={sorted(unknown)} missing={sorted(missing)}")
    for section, fields in SCHEMA.items():
        block = cfg[section]
        if not isinstance(block, dict):
            raise ValueError(f"config.{section} must be a mapping")
        unknown = set(block) - set(fields)
        missing = set(fields) - set(block)
        if unknown or missing:
            raise ValueError(f"config.{section}: unknown={sorted(unknown)} missing={sorted(missing)}")
        for key, typ in fields.items():
            value = block[key]
            # bool is a subclass of int; never accept it for a numeric field
            if isinstance(value, bool) or not isinstance(value, typ):
                raise ValueError(f"config.{section}.{key}={value!r} has wrong type")
    if not cfg["binance"]["base_urls"]:
        raise ValueError("config.binance.base_urls is empty")
    if min(cfg["upbit"]["sleep_sec"], cfg["binance"]["sleep_sec"], cfg["bithumb"]["sleep_sec"]) < 0:
        raise ValueError("sleep_sec must be >= 0")
    for key in ("period.start", "bithumb.start"):
        section, field = key.split(".")
        try:
            pd.Timestamp(cfg[section][field])
        except ValueError as exc:
            raise ValueError(f"config.{key} is not a date") from exc
    for key in ("return_window_days", "turnover_baseline_days", "fx_vol_fixings"):
        if cfg["drivers"][key] < 2:
            raise ValueError(f"config.drivers.{key} must be >= 2")
    if cfg["drivers"]["newey_west_lags"] < 0 or not cfg["drivers"]["sample_starts"]:
        raise ValueError("config.drivers: newey_west_lags must be >= 0 and sample_starts non-empty")
    for d in cfg["drivers"]["sample_starts"]:
        pd.Timestamp(d)
    for i, ev in enumerate(cfg["events"]["list"]):
        if not isinstance(ev, dict) or set(ev) != {"time_utc", "label"}:
            raise ValueError(f"config.events.list[{i}] needs exactly time_utc and label")
        pd.Timestamp(ev["time_utc"])
    if cfg["events"]["hours_before"] < 1 or cfg["events"]["hours_after"] < 0:
        raise ValueError("config.events: hours_before must be >= 1 and hours_after >= 0")
    _validate_backtest(cfg["backtest"])
    pd.Timestamp(cfg["binance"]["funding_start"])
    if cfg["fx"]["max_age_days"] < 0 or cfg["sampling"]["price_tolerance_hours"] < 0:
        raise ValueError("max_age_days and price_tolerance_hours must be >= 0")
    return cfg


BACKTEST_FEES = {"binance_spot_taker", "binance_futures_taker", "upbit_trade", "btc_withdrawal_btc",
                 "usdt_withdrawal_usdt", "remit_fee_usd"}


def _validate_backtest(b: dict) -> None:
    def nonneg(v, where):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"config.backtest.{where}={v!r} must be a number >= 0")
    if set(b["transfer_hours"]) != {"btc", "usdt"} or set(b["fees"]) != BACKTEST_FEES:
        raise ValueError("config.backtest: transfer_hours needs btc and usdt; fees needs " + ", ".join(sorted(BACKTEST_FEES)))
    if set(b["sensitivity"]) != {"fx_spreads", "entry_buffers"}:
        raise ValueError("config.backtest.sensitivity needs fx_spreads and entry_buffers")
    for k, v in b["transfer_hours"].items():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError(f"config.backtest.transfer_hours.{k} must be a whole number of hours >= 0")
    for k, v in b["fees"].items():
        nonneg(v, f"fees.{k}")
    for k in ("fx_spreads", "entry_buffers"):
        if not isinstance(b["sensitivity"][k], list) or not b["sensitivity"][k]:
            raise ValueError(f"config.backtest.sensitivity.{k} must be a non-empty list")
        for v in b["sensitivity"][k]:
            nonneg(v, f"sensitivity.{k}")
    for k in ("entry_buffer", "fx_spread", "round_trip_days", "fresh_fx_max_age_hours"):
        nonneg(b[k], k)
    if b["trade_size_usd"] <= 0 or b["annual_remit_cap_usd"] <= 0:
        raise ValueError("config.backtest: trade_size_usd and annual_remit_cap_usd must be > 0")


def load_config(path: Path | str = ROOT / "config.yaml") -> dict:
    with open(path) as f:
        return validate(yaml.safe_load(f))
