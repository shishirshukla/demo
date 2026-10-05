import json

import numpy as np
import pandas as pd
import pytest

from trading_system.data import load_dataset
from trading_system.data.corporate_actions import check_adjustments
from trading_system.data.yfinance_downloader import (
    download_historical,
    normalize_daily,
    save_download,
    yahoo_symbol,
)


def raw_bars():
    return pd.DataFrame(
        {
            "Open": [100.0, 102.0],
            "High": [110.0, 112.0],
            "Low": [90.0, 92.0],
            "Close": [105.0, 108.0],
            "Adj Close": [52.5, 108.0],
            "Volume": [1000.0, 2000.0],
        },
        index=pd.date_range("2024-01-02", periods=2, tz="Asia/Kolkata"),
    )


def universe():
    return pd.DataFrame(
        {
            "symbol": ["TCS", "INFY"],
            "sector": ["Technology", "Technology"],
            "active_from": ["2020-01-01", "2021-01-01"],
            "active_to": [None, None],
        }
    )


def test_nse_mapping():
    assert yahoo_symbol("tcs") == "TCS.NS"
    assert yahoo_symbol("M&M") == "M&M.NS"
    assert yahoo_symbol("TCS.NS") == "TCS.NS"
    assert yahoo_symbol("^NSEI") == "^NSEI"
    with pytest.raises(ValueError):
        yahoo_symbol(" ")


def test_normalize_consistently_adjusts_ohlc_and_turnover():
    f = normalize_daily(raw_bars(), "TCS")
    assert f.open.tolist() == [50.0, 102.0]
    assert f.high.tolist() == [55.0, 112.0]
    assert f.low.tolist() == [45.0, 92.0]
    assert f.close.tolist() == [52.5, 108.0]
    assert f.adjusted_close.equals(f.close)
    assert f.volume.tolist() == [1000.0, 2000.0]
    assert f.turnover.tolist() == [105000.0, 216000.0]
    assert set(f.timestamp.dt.strftime("%H:%M")) == {"15:30"}
    check_adjustments(f)


@pytest.mark.parametrize("ticker_first", [True, False])
def test_multi_index_layouts(ticker_first):
    raw = raw_bars()
    raw.columns = pd.MultiIndex.from_tuples(
        [("TCS.NS", c) if ticker_first else (c, "TCS.NS") for c in raw.columns]
    )
    assert len(normalize_daily(raw, "TCS", "TCS.NS")) == 2


def test_reject_partial_nan_bars_and_missing_adjustments():
    raw = raw_bars()
    raw.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="partial"):
        normalize_daily(raw, "TCS")
    with pytest.raises(ValueError, match="missing columns"):
        normalize_daily(raw_bars().drop(columns="Adj Close"), "TCS")
    blank = pd.DataFrame(
        np.nan,
        index=pd.date_range("2024-01-04", periods=1, tz="Asia/Kolkata"),
        columns=raw_bars().columns,
    )
    assert len(normalize_daily(pd.concat([raw_bars(), blank]), "TCS")) == 2


def test_retries_export_and_loader_roundtrip(tmp_path):
    calls, delays = [], []

    def fetch(ticker, **kwargs):
        calls.append((ticker, kwargs))
        if ticker == "TCS.NS" and sum(t == ticker for t, _ in calls) == 1:
            raise RuntimeError("temporary provider failure")
        return raw_bars()

    result = download_historical(
        universe(),
        "2024-01-01",
        "2024-01-03",
        fetcher=fetch,
        sleeper=delays.append,
        delay=0.1,
        retries=2,
    )
    assert result.manifest["complete_universe"]
    assert result.dataset.daily.symbol.nunique() == 2
    assert len(result.dataset.daily) == 2  # End date is exclusive.
    assert sum(t == "TCS.NS" for t, _ in calls) == 2
    assert calls[0][0] == "^NSEI"
    assert delays == [0.1, 0.1, 0.1]
    save_download(result, tmp_path)
    loaded = load_dataset(
        tmp_path / "daily.csv", tmp_path / "benchmark.csv", tmp_path / "universe.csv"
    )
    pd.testing.assert_frame_equal(loaded.daily, result.dataset.daily, check_dtype=False)
    manifest = json.loads((tmp_path / "download_manifest.json").read_text())
    assert manifest["symbol_mapping"]["INFY"] == "INFY.NS"
    with pytest.raises(FileExistsError):
        save_download(result, tmp_path)


def test_partial_universe_requires_explicit_opt_in():
    def fetch(ticker, **kwargs):
        return pd.DataFrame() if ticker == "INFY.NS" else raw_bars()

    with pytest.raises(ValueError, match="Incomplete universe"):
        download_historical(
            universe(), "2024-01-01", "2024-02-01", fetcher=fetch, delay=0, retries=1
        )
    result = download_historical(
        universe(),
        "2024-01-01",
        "2024-02-01",
        fetcher=fetch,
        delay=0,
        retries=1,
        allow_partial=True,
    )
    assert not result.manifest["complete_universe"]
    assert "stock:INFY" in result.manifest["failures"]
    assert result.dataset.universe.symbol.tolist() == ["TCS"]


def test_benchmark_failure_is_always_fatal():
    def fetch(ticker, **kwargs):
        return pd.DataFrame() if ticker == "^NSEI" else raw_bars()

    with pytest.raises(ValueError, match="Benchmark download failed"):
        download_historical(
            universe(),
            "2024-01-01",
            "2024-02-01",
            fetcher=fetch,
            delay=0,
            retries=1,
            allow_partial=True,
        )


def test_mapping_overrides_membership_and_reject_demo():
    u = universe()
    u["yahoo_symbol"] = ["CUSTOM.NS", "INFY.NS"]
    result = download_historical(
        u, "2024-01-01", "2024-02-01", fetcher=lambda *a, **k: raw_bars(), delay=0
    )
    assert result.manifest["symbol_mapping"]["TCS"] == "CUSTOM.NS"
    assert result.dataset.universe.active_from.iloc[0] == pd.Timestamp("2020-01-01")
    u["index_group"] = "SYNTHETIC"
    with pytest.raises(ValueError, match="fictional"):
        download_historical(u, "2024-01-01", "2024-02-01")
