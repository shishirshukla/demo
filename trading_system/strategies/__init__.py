from .base import BaseStrategy
from .momentum_pullback import MomentumPullback
from .opening_momentum import OpeningMomentum
from .volatility_breakout import VolatilityBreakout
from .vwap_reversion import VWAPReversion
from .weakness_breakdown import WeaknessBreakdown

REGISTRY = {
    cls.name: cls
    for cls in [
        MomentumPullback,
        VolatilityBreakout,
        WeaknessBreakdown,
        VWAPReversion,
        OpeningMomentum,
    ]
}


def register_strategy(cls):
    if not issubclass(cls, BaseStrategy) or cls.name in REGISTRY:
        raise ValueError("Strategy must inherit BaseStrategy and have a unique name")
    REGISTRY[cls.name] = cls
    return cls


def build_strategies(settings):
    unknown = set(settings["strategies"]) - set(REGISTRY)
    if unknown:
        raise ValueError(f"Unregistered strategies: {sorted(unknown)}")
    return [
        REGISTRY[name](p)
        for name, p in settings["strategies"].items()
        if p.get("enabled", False)
    ]
