"""USD/KRW from FRED series DEXKOUS (noon buying rate in New York, KRW per USD).

FRED marks holidays with an empty value (older exports used "."); those rows
are dropped here and handled explicitly as stale-FX days in build_dataset.
"""
import io
import logging

import pandas as pd

from .net import HttpClient

log = logging.getLogger(__name__)


def parse_fred_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text))
    if raw.shape[1] != 2:
        raise ValueError(f"unexpected FRED columns: {list(raw.columns)}")
    df = pd.DataFrame({
        "date": pd.to_datetime(raw.iloc[:, 0]).dt.as_unit("ns"),
        "usdkrw": pd.to_numeric(raw.iloc[:, 1], errors="coerce"),
    })
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if (df["usdkrw"] <= 0).any():
        raise ValueError("non-positive FX rate in FRED data")
    return df


def fetch_fred(client: HttpClient, url: str, start: str) -> pd.DataFrame:
    df = parse_fred_csv(client.get(url).text)
    df = df[df["date"] >= pd.Timestamp(start) - pd.Timedelta(days=14)].reset_index(drop=True)
    log.info("FRED FX: %d fixings %s -> %s", len(df), df["date"].iloc[0].date(), df["date"].iloc[-1].date())
    return df
