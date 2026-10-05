from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class Regime:
    label: str = "FLAT"
    directional: bool = False
    breadth: float = 0.5
    adx: float = 0


@dataclass
class Signal:
    timestamp: pd.Timestamp
    symbol: str
    strategy: str
    side: Literal["LONG", "SHORT"]
    entry_price: float
    stop_price: float
    target_price: float | None = None
    score: float = 0
    metadata: dict = field(default_factory=dict)
    order_type: str = "MARKET"


@dataclass
class Order:
    signal: Signal
    quantity: int
    fill_price: float


@dataclass
class Position:
    signal: Signal
    quantity: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    entry_cost: float
    initial_risk: float
    sessions: int = 0
    last_session: object = None
    below_ema: int = 0

    @property
    def direction(self):
        return 1 if self.signal.side == "LONG" else -1


@dataclass
class Trade:
    symbol: str
    strategy: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    r_multiple: float
    reason: str
    sector: str
    regime: str
    volatility_quartile: int
    liquidity_quartile: int
