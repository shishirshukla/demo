import logging
from dataclasses import asdict, dataclass

import pandas as pd

from trading_system.data.features import (
    daily_features,
    eligible_rows,
    intraday_features,
)
from trading_system.models import Position, Trade
from trading_system.portfolio import RiskManager
from trading_system.regime import RegimeClassifier
from trading_system.strategies import build_strategies

from .costs import CostModel
from .fills import entry_fill, protective_fill

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    regimes: pd.DataFrame
    rejections: dict
    initial_capital: float
    slippage_bps: float
    synthetic: bool
    settings: dict


class BacktestEngine:
    """Daily or hybrid event loop. All entries pass through RiskManager.

    Indicators are evaluated at bar close; orders expire after the next eligible
    bar. Hybrid runs execute swing orders at the next session's first 5m open.
    """

    def __init__(self, settings, strategies=None):
        self.settings = settings
        self.strategies = (
            strategies if strategies is not None else build_strategies(settings)
        )

    def run(self, dataset, slippage_bps=5, start=None, end=None, mode="daily"):
        if mode not in ("daily", "hybrid", "intraday"):
            raise ValueError("mode must be daily, intraday, or hybrid")
        dataset.validate()
        intraday_mode = mode != "daily"
        if intraday_mode and dataset.intraday is None:
            raise ValueError("Intraday/hybrid mode requires 5-minute bars")
        self.cost = CostModel(self.settings["costs"], slippage_bps)
        self.risk = RiskManager(self.settings["portfolio"])
        self.positions, self.pending, self.exit_pending = {}, [], {}
        self.marks, self.trades, self.curve, self.regimes = {}, [], [], []
        self.capital = float(self.settings["portfolio"]["initial_capital"])
        self.realized = 0.0
        self.volume_today = 0.0
        self.turnover_day = None
        self.strategy_map = {s.name: s for s in self.strategies}
        classifier = RegimeClassifier(self.settings["regime"])
        benchmark = classifier.prepare(dataset.benchmark)
        daily = daily_features(dataset.daily, benchmark, self.settings)
        daily_groups = {t: f for t, f in daily.groupby("timestamp")}
        benchmark_rows = {r.timestamp: r for _, r in benchmark.iterrows()}
        times = sorted(daily_groups)
        daily_snapshots, regime_map = {}, {}
        for t in times:
            eligible = eligible_rows(
                daily_groups[t], dataset.universe, t, self.settings
            )
            daily_snapshots[t] = eligible
            regime_map[t] = classifier.classify(benchmark_rows.get(t), eligible)
        events = [(t, 1, "daily", f) for t, f in daily_groups.items()]
        intraday_bench = {}
        if intraday_mode:
            bars = intraday_features(dataset.intraday, self.settings)
            # Explicit complete-session contract prevents invented end-of-day fills.
            for _, session in bars.groupby(["symbol", "session"]):
                expected = set(range(560, 931, 5))
                if set(session.minute) != expected:
                    raise ValueError(
                        "Intraday research requires complete 09:20–15:30 close-timestamp sessions per symbol (75 bars)"
                    )
            events += [(t, 0, "intraday", f) for t, f in bars.groupby("timestamp")]
            if dataset.benchmark_intraday is not None:
                bf = intraday_features(dataset.benchmark_intraday, self.settings)
                for _, r in bf.iterrows():
                    prior = benchmark[benchmark.timestamp < r.timestamp]
                    r["previous_close"] = (
                        prior.iloc[-1].close if len(prior) else float("nan")
                    )
                    intraday_bench[r.timestamp] = r
            first_exec, last_exec = bars.timestamp.min(), bars.timestamp.max()
        else:
            first_exec, last_exec = daily.timestamp.min(), daily.timestamp.max()
        lower = (
            pd.Timestamp(start, tz="Asia/Kolkata") if start else first_exec.normalize()
        )
        upper = (
            pd.Timestamp(end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
            if end
            else last_exec + pd.Timedelta(seconds=1)
        )
        events = [
            e
            for e in events
            if lower <= e[0] < upper
            and (not intraday_mode or first_exec.normalize() <= e[0] <= last_exec)
        ]
        if not events:
            raise ValueError("No tradable data in requested date range")
        self.execution_rows = {}
        for t, _, kind, raw in sorted(events, key=lambda e: (e[0], e[1])):
            if self.turnover_day != t.date():
                self.turnover_day, self.volume_today = t.date(), 0
            # Start-of-session equity uses preceding known prices, before the gap.
            self.risk.update(t, self.equity())
            if kind == "daily":
                rows = daily_snapshots[t]
                regime = regime_map[t]
                b = benchmark_rows.get(t)
            else:
                prior_times = [x for x in times if x < t]
                prior_t = prior_times[-1] if prior_times else None
                if prior_t is None:
                    continue
                snapshot = daily_snapshots[prior_t].rename(
                    columns={
                        "close": "daily_close",
                        "open": "daily_open",
                        "high": "daily_high",
                        "low": "daily_low",
                        "volume": "daily_volume",
                        "timestamp": "daily_timestamp",
                    }
                )
                keep = [
                    x for x in snapshot.columns if x not in raw.columns or x == "symbol"
                ]
                rows = raw.merge(snapshot[keep], on="symbol", how="inner")
                # No stale daily signal context across gaps in supplied daily data.
                if (t.date() - prior_t.date()).days > 7:
                    raise ValueError("Daily context is stale for intraday session")
                regime = regime_map[prior_t]
                b = intraday_bench.get(t)
                if len(rows):
                    rows["sector_return"] = rows.groupby(
                        "sector"
                    ).session_return.transform("mean")
                    rows["residual"] = rows.session_return - rows.sector_return
            self.regimes.append(
                {
                    "timestamp": t,
                    "label": regime.label,
                    "breadth": regime.breadth,
                    "adx": regime.adx,
                    "directional": regime.directional,
                }
            )
            execution = kind == "intraday" if intraday_mode else kind == "daily"
            raw_by_symbol = {r.symbol: r for _, r in raw.iterrows()}
            if execution:
                for symbol, row in raw_by_symbol.items():
                    self.marks[symbol] = float(row.open)
                self.risk.update(t, self.equity())
                # Close-confirmed discretionary exits fill on the next bar open.
                for symbol, reason in list(self.exit_pending.items()):
                    if symbol in self.positions and symbol in raw_by_symbol:
                        self.close(symbol, raw_by_symbol[symbol].open, t, reason)
                for symbol in list(self.positions):
                    if symbol not in raw_by_symbol:
                        continue
                    position, row = self.positions[symbol], raw_by_symbol[symbol]
                    # Only open gaps may release slots before entry orders.
                    # Intrabar stops/targets become known after entry sizing.
                    open_bar = row.copy()
                    open_bar["high"] = open_bar["low"] = row.open
                    price, reason = protective_fill(position, open_bar)
                    if reason:
                        self.close(symbol, price, t, reason)
                queued, self.pending = self.pending, []
                date = t.tz_localize(None).normalize()
                membership = dataset.universe
                active_symbols = set(
                    membership.loc[
                        (
                            membership.active_from.isna()
                            | (membership.active_from <= date)
                        )
                        & (
                            membership.active_to.isna() | (membership.active_to >= date)
                        ),
                        "symbol",
                    ]
                )
                new_symbols = set()
                for signal in sorted(
                    queued, key=lambda s: (-s.score, s.strategy, s.symbol)
                ):
                    if signal.symbol not in raw_by_symbol:
                        continue
                    if signal.symbol not in active_symbols:
                        self.risk.rejections["inactive_membership"] += 1
                        continue
                    if signal.metadata["timeframe"] == "intraday":
                        cutoff = signal.metadata.get("force_exit", "15:10")
                        if (
                            t.date() != signal.timestamp.date()
                            or t.strftime("%H:%M") >= cutoff
                            or t - signal.timestamp > pd.Timedelta(minutes=5)
                        ):
                            continue
                    elif (
                        t.date() <= signal.timestamp.date()
                        or (t.date() - signal.timestamp.date()).days > 7
                    ):
                        continue
                    bar = raw_by_symbol[signal.symbol]
                    raw_fill = entry_fill(signal, bar)
                    if raw_fill is None:
                        continue
                    price = self.cost.execution_price(raw_fill, signal.side == "LONG")
                    intra = signal.metadata["timeframe"] == "intraday"

                    def fee(qty):
                        return self.cost.fees(price, qty, signal.side == "LONG", intra)

                    order = self.risk.approve(
                        signal,
                        price,
                        self.positions,
                        self.equity(),
                        self.exposure(),
                        fee,
                    )
                    if order is None:
                        continue
                    position = Position(
                        signal,
                        order.quantity,
                        t,
                        price,
                        signal.stop_price,
                        fee(order.quantity),
                        abs(price - signal.stop_price) * order.quantity,
                    )
                    self.positions[signal.symbol] = position
                    new_symbols.add(signal.symbol)
                    self.realized -= position.entry_cost
                    self.volume_today += price * order.quantity
                    self.risk.update(t, self.equity())
                # Evaluate the bar's full range only after all entry orders are
                # sized; later price action cannot free entry capacity at open.
                for symbol, position in list(self.positions.items()):
                    if symbol not in raw_by_symbol:
                        continue
                    exit_price, reason = protective_fill(
                        position, raw_by_symbol[symbol]
                    )
                    if reason == "stop" or (
                        reason == "target"
                        and (
                            symbol not in new_symbols
                            or position.signal.order_type == "MARKET"
                        )
                    ):
                        self.close(symbol, exit_price, t, reason)
                for symbol, row in raw_by_symbol.items():
                    self.marks[symbol] = float(row.close)
                    self.execution_rows[symbol] = (t, row)
                for symbol in list(self.positions):
                    p = self.positions[symbol]
                    if (
                        p.signal.metadata["timeframe"] == "intraday"
                        and symbol in raw_by_symbol
                        and t.strftime("%H:%M")
                        >= p.signal.metadata.get("force_exit", "15:10")
                    ):
                        self.close(
                            symbol, raw_by_symbol[symbol].close, t, "session_exit"
                        )
            # Daily trailing stops are updated after the day's prices are known.
            # Intraday exits use closing values, then next-bar open execution.
            feature_by_symbol = {r.symbol: r for _, r in rows.iterrows()}
            for symbol, p in list(self.positions.items()):
                strategy = self.strategy_map[p.signal.strategy]
                if strategy.timeframe != kind or symbol not in feature_by_symbol:
                    continue
                r = feature_by_symbol[symbol]
                if kind == "daily":
                    if p.last_session != t.date():
                        p.sessions += 1
                        p.last_session = t.date()
                    multiple = p.signal.metadata.get(
                        "atr_trail", p.signal.metadata.get("trail_atr")
                    )
                    if multiple is not None and pd.notna(r.atr):
                        p.stop_price = max(p.stop_price, r.close - multiple * r.atr)
                reason = strategy.exit_reason(p, r)
                if kind == "daily" and p.sessions >= p.signal.metadata.get(
                    "max_holding_days", 10
                ):
                    reason = "maximum_holding"
                if reason:
                    self.exit_pending[symbol] = reason
            for strategy in self.strategies:
                if strategy.timeframe != kind or (
                    mode == "intraday" and kind == "daily"
                ):
                    continue
                if kind == "intraday" and t.strftime(
                    "%H:%M"
                ) >= strategy.parameters.get("force_exit", "15:10"):
                    continue
                self.pending += strategy.generate_signals(rows, b, regime, t)
            self.risk.update(t, self.equity())
            self.curve.append(
                {
                    "timestamp": t,
                    "equity": self.equity(),
                    "exposure": self.exposure(),
                    "positions": len(self.positions),
                    "turnover": self.volume_today,
                    "sector_exposure": self.sector_exposure(),
                    "strategy_exposure": self.strategy_exposure(),
                }
            )
        for symbol in list(self.positions):
            t, row = self.execution_rows[symbol]
            self.close(symbol, row.close, t, "end_of_data")
        self.curve[-1].update(
            equity=self.equity(),
            exposure=0,
            positions=0,
            turnover=self.volume_today,
            sector_exposure={},
            strategy_exposure={},
        )
        log.info(
            "Backtest complete: %s trades, %s bps, equity %.2f",
            len(self.trades),
            slippage_bps,
            self.equity(),
        )
        columns = list(Trade.__dataclass_fields__)
        return BacktestResult(
            pd.DataFrame(self.curve),
            pd.DataFrame([asdict(t) for t in self.trades], columns=columns),
            pd.DataFrame(self.regimes),
            dict(self.risk.rejections),
            self.capital,
            slippage_bps,
            dataset.synthetic,
            self.settings,
        )

    def equity(self):
        return (
            self.capital
            + self.realized
            + sum(
                (self.marks.get(s, p.entry_price) - p.entry_price)
                * p.quantity
                * p.direction
                for s, p in self.positions.items()
            )
        )

    def exposure(self):
        return sum(
            self.marks.get(s, p.entry_price) * p.quantity
            for s, p in self.positions.items()
        )

    def sector_exposure(self):
        result = {}
        for s, p in self.positions.items():
            sector = p.signal.metadata.get("sector", "Unknown")
            result[sector] = (
                result.get(sector, 0) + self.marks.get(s, p.entry_price) * p.quantity
            )
        return result

    def strategy_exposure(self):
        result = {}
        for s, p in self.positions.items():
            name = p.signal.strategy
            result[name] = (
                result.get(name, 0) + self.marks.get(s, p.entry_price) * p.quantity
            )
        return result

    def close(self, symbol, raw_price, timestamp, reason):
        p = self.positions.pop(symbol)
        price = self.cost.execution_price(float(raw_price), p.direction == -1)
        fee = self.cost.fees(
            price,
            p.quantity,
            p.direction == -1,
            p.signal.metadata["timeframe"] == "intraday",
        )
        gross = (price - p.entry_price) * p.quantity * p.direction
        net = gross - p.entry_cost - fee
        self.realized += gross - fee
        self.volume_today += price * p.quantity
        m = p.signal.metadata
        self.trades.append(
            Trade(
                symbol,
                p.signal.strategy,
                p.signal.side,
                p.entry_time,
                timestamp,
                p.entry_price,
                price,
                p.quantity,
                gross,
                p.entry_cost + fee,
                net,
                net / p.initial_risk,
                reason,
                m.get("sector", "Unknown"),
                m.get("regime", "FLAT"),
                m.get("volatility_quartile", 0),
                m.get("liquidity_quartile", 0),
            )
        )
        self.exit_pending.pop(symbol, None)
