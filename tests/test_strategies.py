import pandas as pd
import pytest

from trading_system.config import load_settings
from trading_system.models import Regime
from trading_system.strategies import (
    MomentumPullback,
    OpeningMomentum,
    VolatilityBreakout,
    VWAPReversion,
    WeaknessBreakdown,
    build_strategies,
)

T = pd.Timestamp("2024-02-01 10:00", tz="Asia/Kolkata")


def frame(**values):
    return pd.DataFrame(
        [
            {
                "symbol": "TEST",
                "sector": "IT",
                "lot_size": 1,
                "volatility_quartile": 2,
                "liquidity_quartile": 3,
                **values,
            }
        ]
    )


def strategy(cls):
    return cls(load_settings()["strategies"][cls.name])


def test_momentum_pullback_signal_and_regime_gate():
    s = strategy(MomentumPullback)
    f = frame(
        close=97,
        prior_high10=100,
        previous_high=96,
        rs20_rank=0.9,
        rs60_rank=0.8,
        sma50=90,
        sma200=80,
        ema20=94,
        atr=2,
        swing_low=94,
    )
    signals = s.generate_signals(f, None, Regime("BULL", True, 0.7, 30), T)
    assert len(signals) == 1
    assert signals[0].stop_price == 94
    assert signals[0].metadata["timeframe"] == "daily"
    assert not s.generate_signals(f, None, Regime("BEAR"), T)


def test_breakout_uses_buffered_stop_order():
    s = strategy(VolatilityBreakout)
    f = frame(
        close=105,
        sma50=99,
        sma200=90,
        vol_rank=0.2,
        atr_rank=0.3,
        breakout_high=104,
        volume=200,
        avg_volume=100,
        atr=2,
        rs20_rank=0.8,
    )
    signal = s.generate_signals(f, None, Regime("FLAT", True, 0.5, 25), T)[0]
    assert signal.order_type == "STOP"
    assert signal.entry_price == pytest.approx(104.2)
    assert signal.stop_price == pytest.approx(101.2)
    assert not s.generate_signals(f, None, Regime("FLAT", False), T)


def test_weakness_breakdown_short_valid_stop_and_target():
    s = strategy(WeaknessBreakdown)
    f = frame(
        close=90,
        daily_close=95,
        sma20=98,
        sma50=100,
        rs20_rank=0.1,
        support=92,
        vwap=93,
        relative_volume=2,
        atr_intraday=1,
    )
    signal = s.generate_signals(f, None, Regime("BEAR", True, 0.3, 30), T)[0]
    assert signal.side == "SHORT"
    assert signal.stop_price == 91.5
    assert signal.target_price == 86.25


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_vwap_reversion_both_directions(side):
    s = strategy(VWAPReversion)
    if side == "LONG":
        f = frame(
            close=96,
            vwap=100,
            std=1,
            rsi5=20,
            residual=-0.01,
            previous_high_intraday=95,
            previous_low_intraday=94,
            recent_low=93,
            recent_high=99,
            atr_intraday=1,
        )
    else:
        f = frame(
            close=104,
            vwap=100,
            std=1,
            rsi5=80,
            residual=0.01,
            previous_high_intraday=106,
            previous_low_intraday=105,
            recent_low=100,
            recent_high=107,
            atr_intraday=1,
        )
    signal = s.generate_signals(f, None, Regime("FLAT", False, 0.5, 15), T)[0]
    assert signal.side == side
    assert signal.target_price == 100
    assert not s.generate_signals(f, None, Regime("FLAT", True, 0.5, 25), T)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_opening_momentum_both_directions_and_benchmark_required(side):
    s = strategy(OpeningMomentum)
    if side == "LONG":
        f = frame(
            close=105,
            daily_close=100,
            vwap=102,
            rs20=0.1,
            rs20_rank=0.9,
            relative_volume=2,
            or_high=104,
            or_low=98,
        )
        benchmark = pd.Series({"close": 110, "previous_close": 100, "vwap": 105})
        regime = Regime("BULL", True, 0.7, 30)
    else:
        f = frame(
            close=95,
            daily_close=100,
            vwap=98,
            rs20=-0.1,
            rs20_rank=0.1,
            relative_volume=2,
            or_high=102,
            or_low=96,
        )
        benchmark = pd.Series({"close": 90, "previous_close": 100, "vwap": 95})
        regime = Regime("BEAR", True, 0.3, 30)
    signal = s.generate_signals(f, benchmark, regime, T)[0]
    assert signal.side == side
    assert signal.stop_price == (101 if side == "LONG" else 99)
    assert not s.generate_signals(f, None, regime, T)
    benchmark["vwap"] = float("nan")
    assert not s.generate_signals(f, benchmark, regime, T)
    assert s.last_counts["benchmark_vwap_unavailable"] == 1


def test_registry_enables_each_strategy_independently():
    config = load_settings()
    for name in config["strategies"]:
        for key, value in config["strategies"].items():
            value["enabled"] = key == name
        enabled = build_strategies(config)
        assert len(enabled) == 1 and enabled[0].name == name
