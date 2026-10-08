import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from trading_system.backtest import BacktestEngine
from trading_system.backtest.preparation import prepare_backtest
from trading_system.config import load_settings
from trading_system.data import demo_dataset
from trading_system.data.converted import converted_catalog, load_converted_dataset
from trading_system.models import Signal
from trading_system.strategies.base import BaseStrategy


class DailyProbe(BaseStrategy):
    name = "probe"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if timestamp != self.parameters["date"]:
            return []
        row = market_data.iloc[0]
        # Two orders for one symbol exercise approval and duplicate rejection.
        return [
            self.make_signal(row, timestamp, regime, "LONG", row.close * 0.8, 1)
            for _ in range(2)
        ]


def test_screening_retains_long_history_and_traces_order_outcomes():
    ds = demo_dataset(sessions=230, intraday_sessions=2)
    times = sorted(ds.daily.timestamp.unique())
    ds.daily = ds.daily.loc[
        ds.daily.symbol.ne("DEMO08") | ds.daily.timestamp.ge(times[-2])
    ]
    updates = []
    settings = load_settings()
    prepared = prepare_backtest(ds, settings, mode="hybrid")
    r = BacktestEngine(settings, [DailyProbe({"date": times[-2]})]).run(
        ds,
        mode="hybrid",
        prepared=prepared,
        progress=lambda f, m: updates.append((f, m)),
    )
    screen = r.daily_screener
    assert screen.groupby("symbol").size()["DEMO01"] == 230
    assert screen.groupby("symbol").size()["DEMO08"] == 2
    last = screen.loc[screen.symbol.eq("DEMO01")].iloc[-1]
    assert last.daily_history_sessions == 230 and pd.notna(last.sma200)
    assert len(r.trades) == 1
    assert set(r.signal_log.status) == {"filled", "rejected"}
    assert r.signal_log.loc[r.signal_log.status.eq("rejected"), "reason"].tolist() == [
        "duplicate_symbol"
    ]
    assert r.trades.entry_time.iloc[0].strftime("%H:%M") == "09:20"
    assert r.trades.signal_time.iloc[0] == times[-2]
    assert (
        r.trades.signal_id.iloc[0]
        == r.signal_log.loc[r.signal_log.status.eq("filled"), "signal_id"].iloc[0]
    )
    assert updates[-1][0] == 1 and [f for f, _ in updates] == sorted(
        f for f, _ in updates
    )
    assert r.signal_log.iloc[0].features["timestamp"] == times[-2]
    with pytest.raises(ValueError, match="Prepared features"):
        BacktestEngine(settings).run(ds, mode="daily", prepared=prepared)

    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    app.session_state["results"] = [r]
    app.session_state["last_dataset"] = ds
    app.session_state["last_mode"] = "hybrid"
    app.session_state["last_range"] = (None, None)
    app.run()
    next(v for v in app.radio if v.label == "Result screen").set_value(
        "Stock trade details"
    ).run()
    assert not app.exception and not app.error
    assert any(v.label == "Inspect triggered signal" for v in app.selectbox)


class IntradayProbe(BaseStrategy):
    name = "intraday_probe"
    timeframe = "intraday"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if timestamp.strftime("%H:%M") != "10:00" or market_data.empty:
            return []
        row = market_data.iloc[0]
        return [
            Signal(
                timestamp,
                row.symbol,
                self.name,
                "LONG",
                row.close,
                row.close * 0.5,
                metadata={"timeframe": "intraday", "sector": row.sector},
            )
        ]


def test_intraday_trigger_uses_strictly_prior_stock_daily_context():
    ds = demo_dataset(sessions=220, intraday_sessions=2)
    times = sorted(ds.daily.timestamp.unique())
    # One stock lacks the immediately preceding daily candle; use its own last
    # observed candle, not today's completed daily OHLC or another stock's bar.
    ds.daily = ds.daily.loc[
        ~(ds.daily.symbol.eq("DEMO01") & ds.daily.timestamp.eq(times[-3]))
    ]
    r = BacktestEngine(load_settings(), [IntradayProbe({})]).run(ds, mode="intraday")
    for row in r.signal_log.itertuples():
        assert row.screen_timestamp < row.timestamp
        assert row.features["daily_timestamp"] == row.screen_timestamp
        assert row.features["timestamp"] == row.timestamp
    first = r.signal_log.iloc[0]
    assert first.screen_timestamp == times[-4]
    assert first.execution_time - first.timestamp == pd.Timedelta(minutes=5)


def converted_fixture(root):
    ds = demo_dataset(sessions=220, intraday_sessions=2)
    root.mkdir()
    coverage = []
    for timeframe, data in [
        ("daily", pd.concat([ds.daily, ds.benchmark])),
        ("5minute", pd.concat([ds.intraday, ds.benchmark_intraday])),
    ]:
        (root / timeframe).mkdir()
        for symbol, f in data.groupby("symbol"):
            f.to_csv(root / timeframe / f"{symbol}.csv", index=False)
            if timeframe == "daily":
                coverage.append(
                    {
                        "symbol": symbol,
                        "filename": f"{symbol}.csv",
                        "daily_rows": len(f),
                    }
                )
    (root / "conversion_manifest.json").write_text(
        json.dumps(
            {"format_version": 1, "timezone": "Asia/Kolkata", "coverage": coverage}
        )
    )
    return root


def test_generated_files_keep_other_stocks_when_one_session_is_incomplete(tmp_path):
    folder = converted_fixture(tmp_path / "candles")
    path = folder / "5minute/DEMO02.csv"
    pd.read_csv(path).iloc[1:].to_csv(path, index=False)
    updates = []
    ds, coverage = load_converted_dataset(
        folder,
        ["DEMO01", "DEMO02"],
        "NIFTY50",
        mode="hybrid",
        progress=lambda f, m: updates.append(f),
    )
    assert ds.daily.groupby("symbol").size().to_dict() == {"DEMO01": 220, "DEMO02": 220}
    assert ds.intraday.groupby("symbol").size().to_dict() == {
        "DEMO01": 150,
        "DEMO02": 75,
    }
    assert coverage["available_sessions"] == 220
    assert updates[-1] == 1 and updates == sorted(updates)
    assert converted_catalog(tmp_path)[0]["symbols"]


def test_dashboard_generated_screens_and_signal_drilldown(tmp_path, monkeypatch):
    import frontend.converted_backtest as menu

    converted_fixture(tmp_path / "candles")
    monkeypatch.setattr(menu, "CONVERTED_ROOT", tmp_path)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Data source").set_value(
        "Generated Kite candles"
    ).run()
    next(b for b in app.button if b.label == "Load selected data").click().run()
    next(b for b in app.button if b.label.startswith("Run backtest")).click().run(
        timeout=120
    )
    assert not app.exception and not app.error
    assert app.session_state["last_mode"] == "hybrid"
    next(r for r in app.radio if r.label == "Result screen").set_value(
        "Daily screener"
    ).run()
    assert not app.exception
    assert any(s.label == "Screening session" for s in app.selectbox)
    next(r for r in app.radio if r.label == "Result screen").set_value(
        "Stock trade details"
    ).run()
    assert not app.exception
    assert any(s.label == "Stock" for s in app.selectbox)
