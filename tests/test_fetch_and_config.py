"""Pagination against fake APIs that follow the documented contracts (no network)."""
import copy

import pandas as pd
import pytest
import requests

from src.config import load_config, validate
from src.fetch_binance import fetch_klines
from src.fetch_fx import parse_fred_csv
from src.fetch_krw import fetch_candles


class FakeUpbit:
    """newest first, max `count`, `to` exclusive on open time."""

    def __init__(self, opens):
        self.opens = sorted(opens)
        self.calls = 0

    def get_json(self, url, params):
        self.calls += 1
        to = pd.Timestamp(params["to"])
        rows = [t for t in self.opens if t < to][::-1][: params["count"]]
        return [{"candle_date_time_utc": t.strftime("%Y-%m-%dT%H:%M:%S"), "opening_price": 1, "high_price": 2,
                 "low_price": 0.5, "trade_price": t.hour + 1, "candle_acc_trade_volume": 1,
                 "candle_acc_trade_price": 1} for t in rows]


def test_upbit_pages_back_to_listing_with_gaps():
    listing = pd.Timestamp("2024-01-01 05:00", tz="UTC")
    opens = [t for t in pd.date_range("2024-01-01 05:00", "2024-01-10", freq="h", tz="UTC", inclusive="left")
             if t != pd.Timestamp("2024-01-03 12:00", tz="UTC")]            # an hour with no trades
    api = FakeUpbit(opens)
    df = fetch_candles(api, "x", "KRW-BTC", "1h", "2023-12-01", "2024-01-10")
    assert len(df) == len(opens) and df["open_time"].is_unique and df["open_time"].is_monotonic_increasing
    assert df["open_time"].iloc[0] == listing and df["open_time"].iloc[-1] == pd.Timestamp("2024-01-09 23:00", tz="UTC")
    assert (df["close_time"] - df["open_time"] == pd.Timedelta(hours=1)).all()
    assert api.calls == -(-len(opens) // 200) + 1                           # last call returns [] at listing


def test_upbit_respects_start():
    api = FakeUpbit(list(pd.date_range("2024-01-01", "2024-02-01", freq="D", tz="UTC", inclusive="left")))
    df = fetch_candles(api, "x", "KRW-BTC", "1d", "2024-01-15", "2024-01-20")
    assert df["open_time"].dt.day.tolist() == [15, 16, 17, 18, 19]


def test_upbit_stuck_pagination_raises():
    class Stuck(FakeUpbit):
        def get_json(self, url, params):
            return super().get_json(url, {**params, "to": "2024-01-05T00:00:00Z"})
    with pytest.raises(RuntimeError, match="stuck"):
        fetch_candles(Stuck(list(pd.date_range("2024-01-01", "2024-01-05", freq="h", tz="UTC"))),
                      "x", "KRW-BTC", "1h", "2023-01-01", "2024-02-01")


class FakeBinance:
    def __init__(self, opens, blocked=()):
        self.ms = [int(t.timestamp() * 1000) for t in sorted(opens)]
        self.blocked = blocked

    def get_json(self, url, params):
        if any(url.startswith(b) for b in self.blocked):
            resp = requests.Response()
            resp.status_code = 451
            raise requests.HTTPError("451 geo-blocked", response=resp)
        rows = [m for m in self.ms if params["startTime"] <= m <= params["endTime"]][: params["limit"]]
        return [[m, "1", "2", "0.5", "1.5", "3", m + 3_599_999, "4.5", 1, "0", "0", "0"] for m in rows]


def test_binance_pages_forward_and_falls_back_on_geo_block():
    opens = pd.date_range("2024-01-01", "2024-03-01", freq="h", tz="UTC", inclusive="left")
    api = FakeBinance(opens, blocked=("https://blocked",))
    df = fetch_klines(api, ["https://blocked", "https://ok"], "BTCUSDT", "1h", "2024-01-01", "2024-02-15")
    expected = pd.date_range("2024-01-01", "2024-02-15", freq="h", tz="UTC", inclusive="left")
    assert len(expected) > 1000                                             # forces a second page
    assert df["open_time"].tolist() == list(expected)
    assert df["quote_volume"].iloc[0] == 4.5 and df["close"].iloc[0] == 1.5


def test_fred_parser_drops_holidays_in_both_formats():
    df = parse_fred_csv("observation_date,DEXKOUS\n2024-01-01,\n2024-01-02,1300.5\n2024-01-03,.\n")
    assert df["date"].tolist() == [pd.Timestamp("2024-01-02")] and df["usdkrw"].tolist() == [1300.5]


def test_repo_config_is_valid():
    load_config()


@pytest.mark.parametrize("mutate", [
    lambda c: c.__setitem__("backtest", {}),                          # unknown section
    lambda c: c["fx"].__setitem__("max_age", 4),                      # typo'd key
    lambda c: c["fx"].__setitem__("max_age_days", "4"),               # wrong type
    lambda c: c["fx"].__setitem__("max_age_days", True),              # bool is not an int here
    lambda c: c["sampling"].__setitem__("price_tolerance_hours", -1),
    lambda c: c["binance"].__setitem__("base_urls", []),
    lambda c: c.pop("quality"),
    lambda c: c["backtest"]["fees"].pop("remit_fee_usd"),              # a fee silently missing
    lambda c: c["backtest"]["fees"].__setitem__("upbit_trade", -0.001),
    lambda c: c["backtest"]["transfer_hours"].__setitem__("btc", 1.5),  # the backtest steps in whole hours
    lambda c: c["backtest"].__setitem__("annual_remit_cap_usd", 0),
    lambda c: c["events"]["list"].append({"time_utc": "2020-01-01"}),  # no label
    lambda c: c["drivers"].__setitem__("sample_starts", []),
])
def test_config_rejects_bad_input(mutate):
    cfg = copy.deepcopy(load_config())
    mutate(cfg)
    with pytest.raises(ValueError):
        validate(cfg)


class FakeBithumb(FakeUpbit):
    """Bithumb v1 contract as observed live: rejects "Z" and offsets, reads a naive `to` as KST."""

    def get_json(self, url, params):
        to = params["to"]
        if to.endswith("Z") or "+" in to:
            return {"error": {"name": 400, "message": "Invalid parameter. Check the given value!"}}
        utc = pd.Timestamp(to).tz_localize("Asia/Seoul").tz_convert("UTC")
        return super().get_json(url, {**params, "to": utc.isoformat()})


def test_bithumb_cursor_is_sent_as_kst_and_pages_correctly():
    opens = list(pd.date_range("2024-01-01 05:00", "2024-01-10", freq="h", tz="UTC", inclusive="left"))
    df = fetch_candles(FakeBithumb(opens), "x", "KRW-USDT", "1h", "2023-12-01", "2024-01-10", exchange="bithumb")
    assert df["open_time"].tolist() == opens                     # no 9-hour shift, nothing lost at the end
    with pytest.raises(RuntimeError, match="unexpected response"):  # the Upbit (UTC "Z") format is rejected
        fetch_candles(FakeBithumb(opens), "x", "KRW-USDT", "1h", "2023-12-01", "2024-01-10", exchange="upbit")
