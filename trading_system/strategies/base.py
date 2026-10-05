import math
from abc import ABC, abstractmethod

from trading_system.models import Signal


class BaseStrategy(ABC):
    name = "base"
    timeframe = "daily"

    def __init__(self, parameters):
        self.parameters = parameters

    @abstractmethod
    def generate_signals(
        self, market_data, benchmark_data, regime, timestamp
    ) -> list[Signal]:
        """Receive only currently available feature rows; never submit orders."""

    def exit_reason(self, position, row):
        return None

    def make_signal(
        self,
        row,
        timestamp,
        regime,
        side,
        stop,
        score,
        target=None,
        order_type="MARKET",
        entry=None,
    ):
        entry = float(row.close if entry is None else entry)
        numbers = [entry, stop, score] + ([] if target is None else [target])
        if not all(math.isfinite(float(v)) for v in numbers) or entry <= 0 or stop <= 0:
            return None
        if (side == "LONG" and stop >= entry) or (side == "SHORT" and stop <= entry):
            return None
        if target is not None and (
            (side == "LONG" and target <= entry)
            or (side == "SHORT" and target >= entry)
        ):
            return None
        metadata = {
            "timeframe": self.timeframe,
            "sector": row.get("sector", "Unknown"),
            "regime": regime.label,
            "volatility_quartile": int(row.get("volatility_quartile", 0)),
            "liquidity_quartile": int(row.get("liquidity_quartile", 0)),
            "lot_size": int(row.get("lot_size", 1)),
            **self.parameters,
        }
        return Signal(
            timestamp,
            row.symbol,
            self.name,
            side,
            entry,
            float(stop),
            target,
            float(score),
            metadata,
            order_type,
        )


def compact(signals):
    return [s for s in signals if s is not None]
