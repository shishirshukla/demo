from collections import Counter

from trading_system.models import Order

from .position_sizing import position_size


class RiskManager:
    def __init__(self, settings):
        self.settings = settings
        self.day = self.week = None
        self.day_equity = self.week_equity = 0
        self.day_locked = self.week_locked = False
        self.rejections = Counter()

    def update(self, timestamp, equity):
        day, week = timestamp.date(), timestamp.isocalendar()[:2]
        if week != self.week:
            self.week, self.week_equity, self.week_locked = week, equity, False
        if day != self.day:
            self.day, self.day_equity, self.day_locked = day, equity, False
        self.day_locked |= equity <= self.day_equity * (
            1 - self.settings["daily_loss_limit"]
        )
        self.week_locked |= equity <= self.week_equity * (
            1 - self.settings["weekly_loss_limit"]
        )

    def approve(
        self, signal, fill_price, positions, equity, gross_exposure, entry_cost
    ):
        c = self.settings
        reason = None
        if self.day_locked or self.week_locked:
            reason = "loss_limit"
        elif signal.side == "SHORT" and signal.metadata.get("timeframe") != "intraday":
            reason = "overnight_cash_short"
        elif len(positions) >= c["max_positions"]:
            reason = "position_limit"
        elif signal.symbol in positions:
            reason = "duplicate_symbol"
        elif (
            sum(
                p.signal.metadata.get("sector") == signal.metadata.get("sector")
                for p in positions.values()
            )
            >= c["max_sector_positions"]
        ):
            reason = "sector_limit"
        elif (signal.side == "LONG" and fill_price <= signal.stop_price) or (
            signal.side == "SHORT" and fill_price >= signal.stop_price
        ):
            reason = "invalid_stop_after_gap"
        elif signal.target_price is not None and (
            (signal.side == "LONG" and fill_price >= signal.target_price)
            or (signal.side == "SHORT" and fill_price <= signal.target_price)
        ):
            reason = "target_already_passed"
        if reason:
            self.rejections[reason] += 1
            return None
        available = equity * c["max_gross_exposure"] - gross_exposure
        qty = position_size(
            equity,
            fill_price,
            signal.stop_price,
            c,
            available,
            signal.metadata.get("lot_size", 1),
        )
        lot = signal.metadata.get("lot_size", 1)
        while qty > 0 and (
            qty * fill_price + entry_cost(qty) > available
            or qty * abs(fill_price - signal.stop_price) + entry_cost(qty)
            > equity * c["risk_per_trade"]
        ):
            qty -= lot
        if qty <= 0:
            self.rejections["zero_quantity"] += 1
            return None
        return Order(signal, qty, fill_price)
