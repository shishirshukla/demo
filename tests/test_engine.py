import copy
import json

import pandas as pd
import pytest

from trading_system.backtest import BacktestEngine
from trading_system.backtest.walk_forward import evaluate_walk_forward, windows
from trading_system.config import load_settings
from trading_system.data import demo_dataset
from trading_system.data.validation import validate_bars
from trading_system.models import Signal
from trading_system.reports import export_results
from trading_system.strategies.base import BaseStrategy


class ProbeStrategy(BaseStrategy):
    name = "probe"
    timeframe = "intraday"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if timestamp.strftime("%H:%M") != "10:00" or not len(market_data):
            return []
        row = market_data.iloc[0]
        return [
            Signal(
                timestamp,
                row.symbol,
                self.name,
                "SHORT",
                row.close,
                row.close * 1.20,
                score=1,
                metadata={
                    "timeframe": "intraday",
                    "sector": row.sector,
                    "force_exit": "15:10",
                },
            )
        ]


def test_intraday_next_bar_and_forced_exit():
    ds = demo_dataset(sessions=240, intraday_sessions=2)
    strategy = ProbeStrategy({})
    r = BacktestEngine(load_settings(), [strategy]).run(ds, mode="intraday")
    assert len(r.trades) == 2
    assert set(r.trades.entry_time.dt.strftime("%H:%M")) == {"10:05"}
    assert set(r.trades.exit_time.dt.strftime("%H:%M")) == {"15:10"}
    assert (r.trades.entry_time.dt.date == r.trades.exit_time.dt.date).all()
    assert (r.trades.costs > 0).all()
    assert (r.equity.positions <= 3).all()
    assert r.equity.equity.iloc[-1] == pytest.approx(150000 + r.trades.net_pnl.sum())


def test_all_strategies_can_disable_and_export(tmp_path):
    c = load_settings()
    for p in c["strategies"].values():
        p["enabled"] = False
    ds = demo_dataset(sessions=220, intraday_sessions=0)
    r = BacktestEngine(c).run(ds)
    assert r.trades.empty
    assert (r.equity.equity == 150000).all()
    export_results([r], tmp_path, ds)
    assert (
        json.loads((tmp_path / "slippage_5bps/report.json").read_text())["metrics"][
            "total_return"
        ]
        == 0
    )


def test_demo_reproducible_and_costed(tmp_path):
    c = load_settings()
    ds = demo_dataset(sessions=340, intraday_sessions=0)
    first = BacktestEngine(c).run(ds)
    second = BacktestEngine(c).run(copy.deepcopy(ds))
    pd.testing.assert_frame_equal(first.trades, second.trades)
    assert len(first.trades) > 0
    assert (first.trades.costs > 0).all()
    assert first.equity.equity.iloc[-1] == pytest.approx(
        150000 + first.trades.net_pnl.sum()
    )
    export_results([first], tmp_path, ds)
    assert (tmp_path / "slippage_5bps/monthly_returns.csv").exists()
    assert (tmp_path / "daily_screener.csv").exists()
    signals = pd.read_csv(tmp_path / "slippage_5bps/signals.csv")
    assert len(signals) == len(first.signal_log)
    assert "close" in json.loads(signals.features.iloc[0])
    assert (tmp_path / "slippage_5bps/trigger_diagnostics.csv").exists()


def test_invalid_data_and_incomplete_sessions():
    ds = demo_dataset(sessions=220, intraday_sessions=1)
    bad = ds.daily.copy()
    bad.loc[0, "high"] = 1
    with pytest.raises(ValueError, match="OHLC"):
        validate_bars(bad)
    ds.intraday = ds.intraday.iloc[1:]
    with pytest.raises(ValueError, match="complete"):
        BacktestEngine(load_settings()).run(ds, mode="intraday")


def test_intraday_bars_require_exact_five_minute_closes():
    ds = demo_dataset(sessions=20, intraday_sessions=1)
    ds.intraday.loc[0, "timestamp"] += pd.Timedelta(seconds=30)
    with pytest.raises(ValueError, match="5-minute close timestamps"):
        BacktestEngine(load_settings()).run(ds, mode="hybrid")


def test_walk_forward_chronological_and_train_isolated():
    folds = list(windows("2012-01-01", "2020-12-31", 5, 1, 1))
    assert len(folds) == 4
    assert folds[0].train_end == "2016-12-31"
    assert folds[0].validation_start == "2017-01-01"
    ds = demo_dataset(sessions=650, intraday_sessions=0)
    c = load_settings()
    observed = []

    def selector(train, settings):
        observed.append(train.daily.timestamp.max())
        return settings

    short_folds = list(windows("2022-01-03", "2024-12-31", 1, 1, 1))
    evaluate_walk_forward(ds, c, short_folds[:1], selector=selector)
    assert observed[0] < pd.Timestamp(
        short_folds[0].validation_start, tz="Asia/Kolkata"
    )


class ScheduledDailyStrategy(BaseStrategy):
    name = "scheduled_daily"

    def __init__(self, schedule):
        super().__init__({})
        self.schedule = schedule

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        symbol = self.schedule.get(timestamp)
        if symbol is None:
            return []
        rows = market_data[market_data.symbol == symbol]
        if rows.empty:
            return []
        row = rows.iloc[0]
        return [self.make_signal(row, timestamp, regime, "LONG", row.close * 0.8, 1)]


def test_intrabar_exit_cannot_release_slot_for_open_order():
    ds = demo_dataset(sessions=230, intraday_sessions=0)
    times = sorted(ds.daily.timestamp.unique())
    first, second = "DEMO01", "DEMO02"
    entry_close = ds.daily.loc[
        (ds.daily.timestamp == times[210]) & (ds.daily.symbol == first), "close"
    ].iloc[0]
    mask = (ds.daily.timestamp == times[212]) & (ds.daily.symbol == first)
    ds.daily.loc[mask, "low"] = entry_close * 0.7
    config = load_settings()
    config["portfolio"]["max_positions"] = 1
    scheduled = ScheduledDailyStrategy({times[210]: first, times[211]: second})
    r = BacktestEngine(config, [scheduled]).run(ds)
    assert set(r.trades.symbol) == {first}
    assert r.trades.reason.iloc[0] == "stop"
    assert r.rejections["position_limit"] == 1


def test_expired_membership_rejected_at_fill_time():
    ds = demo_dataset(sessions=230, intraday_sessions=0)
    t = sorted(ds.daily.timestamp.unique())[210]
    ds.universe.loc[ds.universe.symbol == "DEMO01", "active_to"] = t.tz_localize(
        None
    ).normalize()
    scheduled = ScheduledDailyStrategy({t: "DEMO01"})
    r = BacktestEngine(load_settings(), [scheduled]).run(ds)
    assert r.trades.empty
    assert r.rejections["inactive_membership"] == 1
