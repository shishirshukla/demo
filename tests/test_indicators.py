import numpy as np
import pandas as pd

from trading_system.config import load_settings
from trading_system.data import demo_dataset
from trading_system.data.features import daily_features, intraday_features
from trading_system.indicators import adx, atr, breadth, ema, percentile, sma, vwap
from trading_system.regime import RegimeClassifier


def test_moving_averages():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert sma(s, 3).iloc[-1] == 3
    assert np.isclose(ema(s, 3).iloc[-1], 3.125)
    assert np.isnan(sma(s, 3).iloc[1])


def test_wilder_atr_and_adx():
    f = pd.DataFrame(
        {
            "high": np.arange(10.0, 70.0),
            "low": np.arange(8.0, 68.0),
            "close": np.arange(9.0, 69.0),
        }
    )
    assert atr(f).iloc[-1] == 2
    assert np.isclose(adx(f).iloc[-1], 100)
    assert atr(f).iloc[:13].isna().all()


def test_vwap_resets_each_session():
    f = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-01 09:20", "2024-01-01 09:25", "2024-01-02 09:20"]
            ),
            "high": [10, 20, 30],
            "low": [10, 20, 30],
            "close": [10, 20, 30],
            "volume": [1, 3, 2],
        }
    )
    assert vwap(f).tolist() == [10, 17.5, 30]


def test_breadth_and_ranking():
    assert (
        breadth(pd.DataFrame({"close": [11, 9, 15], "sma50": [10, 10, np.nan]})) == 0.5
    )
    assert percentile(pd.Series([3.0, 1.0, 2.0])).tolist() == [1, 1 / 3, 2 / 3]


def test_regime_deterministic():
    classifier = RegimeClassifier(load_settings()["regime"])
    b = pd.Series(
        {"close": 110, "ema_fast": 105, "ema_slow": 100, "slope": 1, "adx": 25}
    )
    u = pd.DataFrame({"close": [11, 12, 9], "sma50": [10, 10, 10]})
    first = classifier.classify(b, u)
    assert first.label == "BULL" and first.directional
    assert first == classifier.classify(b, u)
    b = pd.Series(
        {"close": 90, "ema_fast": 95, "ema_slow": 100, "slope": -1, "adx": 25}
    )
    assert (
        classifier.classify(
            b, pd.DataFrame({"close": [9, 8, 11], "sma50": [10, 10, 10]})
        ).label
        == "BEAR"
    )


def test_daily_features_prefix_invariant_and_shifted_highs():
    ds = demo_dataset(sessions=260, intraday_sessions=0)
    c = load_settings()
    b = RegimeClassifier(c["regime"]).prepare(ds.benchmark)
    full = daily_features(ds.daily, b, c)
    cutoff = ds.benchmark.timestamp.iloc[220]
    prefix = daily_features(
        ds.daily[ds.daily.timestamp <= cutoff], b[b.timestamp <= cutoff], c
    )
    pd.testing.assert_frame_equal(
        full[full.timestamp <= cutoff].reset_index(drop=True), prefix, check_dtype=False
    )
    f = full[full.symbol == "DEMO01"]
    expected = (
        ds.daily[ds.daily.symbol == "DEMO01"]
        .high.rolling(20)
        .max()
        .shift()
        .reset_index(drop=True)
    )
    pd.testing.assert_series_equal(
        f.prior_high20.reset_index(drop=True), expected, check_names=False
    )


def test_opening_range_and_relative_volume_use_only_history():
    ds = demo_dataset(sessions=220, intraday_sessions=8)
    c = load_settings()
    full = intraday_features(ds.intraday, c)
    cut = ds.intraday.timestamp.sort_values().iloc[-300]
    prefix = intraday_features(ds.intraday[ds.intraday.timestamp <= cut], c)
    pd.testing.assert_frame_equal(
        full[full.timestamp <= cut].reset_index(drop=True), prefix, check_dtype=False
    )
    f = full[full.symbol == "DEMO01"]
    assert f.loc[f.minute <= 570, "or_high"].isna().all()
    day = f.session.iloc[-1]
    session = f[f.session == day]
    assert (
        session.loc[session.minute == 575, "or_high"].iloc[0]
        == session.loc[session.minute <= 570, "high"].max()
    )
    assert f[f.session == f.session.iloc[0]].relative_volume.isna().all()
