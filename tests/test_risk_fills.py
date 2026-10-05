import pandas as pd
import pytest

from trading_system.backtest.costs import CostModel
from trading_system.backtest.fills import entry_fill, protective_fill
from trading_system.config import load_settings
from trading_system.models import Position, Signal
from trading_system.portfolio import RiskManager
from trading_system.portfolio.position_sizing import position_size


def signal(symbol="A", side="LONG"):
    return Signal(
        pd.Timestamp("2024-01-01 10:00", tz="Asia/Kolkata"),
        symbol,
        "test",
        side,
        100,
        95 if side == "LONG" else 105,
        metadata={"sector": "IT", "timeframe": "intraday"},
    )


def test_risk_sizing_and_lots():
    c = load_settings()["portfolio"]
    assert position_size(150000, 100, 95, c, 150000) == 150
    assert position_size(150000, 100, 99, c, 150000) == 450
    assert position_size(150000, 100, 95, c, 1000, 4) == 8
    assert position_size(150000, 100, 100, c, 150000) == 0


def test_daily_loss_lock_does_not_reset_after_recovery():
    r = RiskManager(load_settings()["portfolio"])
    t = pd.Timestamp("2024-01-01 09:20", tz="Asia/Kolkata")
    r.update(t, 150000)
    r.update(t, 147750)
    r.update(t, 150000)
    assert r.approve(signal(), 100, {}, 150000, 0, lambda qty: 0) is None
    r.update(t + pd.Timedelta(days=1), 150000)
    assert not r.day_locked


def test_weekly_loss_lock_and_reset():
    r = RiskManager(load_settings()["portfolio"])
    t = pd.Timestamp("2024-01-01", tz="Asia/Kolkata")
    r.update(t, 150000)
    r.update(t + pd.Timedelta(days=1), 144000)
    r.update(t + pd.Timedelta(days=2), 150000)
    assert r.week_locked
    r.update(t + pd.Timedelta(days=7), 150000)
    assert not r.week_locked


def test_sector_limit_duplicate_and_cash_short():
    r = RiskManager(load_settings()["portfolio"])
    s = signal()
    p = Position(s, 1, s.timestamp, 100, 95, 0, 5)
    assert (
        r.approve(signal("C"), 100, {"A": p, "B": p}, 150000, 200, lambda qty: 0)
        is None
    )
    assert r.rejections["sector_limit"] == 1
    assert r.approve(s, 100, {"A": p}, 150000, 100, lambda qty: 0) is None
    short = signal(side="SHORT")
    short.metadata["timeframe"] = "daily"
    assert r.approve(short, 100, {}, 150000, 0, lambda qty: 0) is None


def test_costs_and_friction():
    c = load_settings()["costs"]
    model = CostModel(c, 5)
    buy = model.fees(100, 100, True, True)
    expected = 3 + 0.297 + 0.01 + (3 + 0.297 + 0.01) * 0.18 + 0.3
    assert buy == pytest.approx(expected)
    assert model.fees(100, 100, False, True) - buy == pytest.approx(2.5 - 0.3)
    assert model.execution_price(100, True) == pytest.approx(100.06)
    assert model.fees(100, 100, True, False) > buy


def test_gap_and_ambiguous_bar_stops():
    s = signal()
    p = Position(s, 1, s.timestamp, 100, 95, 0, 5)
    bar = pd.Series({"open": 90, "high": 110, "low": 85, "close": 105})
    assert protective_fill(p, bar) == (90, "stop")
    s.target_price = 105
    bar["open"] = 100
    assert protective_fill(p, bar) == (95, "stop")
    s.order_type = "STOP"
    bar["open"] = 102
    assert entry_fill(s, bar) == 102
    s.order_type = "LIMIT"
    bar["open"] = 98
    assert entry_fill(s, bar) == 98
