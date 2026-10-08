import logging
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict, dataclass, field
from time import monotonic

import pandas as pd

from trading_system.models import Position, Trade
from trading_system.portfolio import RiskManager
from trading_system.strategies import build_strategies

from .costs import CostModel
from .fills import entry_fill, protective_fill
from .preparation import prepare_backtest

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
    daily_screener: pd.DataFrame = field(default_factory=pd.DataFrame)
    signal_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: pd.DataFrame = field(default_factory=pd.DataFrame)


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

    def run(
        self,
        dataset,
        slippage_bps=5,
        start=None,
        end=None,
        mode="daily",
        *,
        progress=None,
        prepared=None,
    ):
        if prepared is None:
            prepared = prepare_backtest(
                dataset,
                self.settings,
                start,
                end,
                mode,
                progress=lambda f, m: progress(f * 0.4, m) if progress else None,
            )
        elif (
            prepared.dataset_id != id(dataset)
            or prepared.settings != self.settings
            or (prepared.start, prepared.end, prepared.mode) != (start, end, mode)
        ):
            raise ValueError(
                "Prepared features do not match this dataset, settings or research period"
            )
        intraday_mode = mode != "daily"
        self.cost = CostModel(self.settings["costs"], slippage_bps)
        self.risk = RiskManager(self.settings["portfolio"])
        self.positions, self.pending, self.exit_pending = {}, [], {}
        self.marks, self.trades, self.curve, self.regimes = {}, [], [], []
        self.capital = float(self.settings["portfolio"]["initial_capital"])
        self.realized = 0.0
        self.volume_today = 0.0
        self.turnover_day = None
        self.strategy_map = {s.name: s for s in self.strategies}
        daily_snapshots, regime_map = prepared.snapshots, prepared.regimes
        benchmark_rows, intraday_bench = (
            prepared.benchmark_rows,
            prepared.intraday_benchmark,
        )
        times = sorted(daily_snapshots)
        events = prepared.events
        self.signal_records, self.signal_records_by_id = [], {}
        self.diagnostic_counts = Counter()
        last_progress = 0.0
        self.execution_rows = {}
        for event_index, (t, _, kind, indices) in enumerate(events):
            raw = (prepared.daily if kind == "daily" else prepared.bars).iloc[indices]
            if progress and (monotonic() - last_progress >= 0.4 or event_index == 0):
                progress(
                    0.4 + 0.56 * event_index / len(events),
                    f"{slippage_bps:g} bps · {t:%Y-%m-%d %H:%M} · {event_index + 1:,}/{len(events):,} events · "
                    f"{len(self.signal_records):,} signals · {len(self.trades):,} closed trades · {len(self.positions)} open",
                )
                last_progress = monotonic()
            if self.turnover_day != t.date():
                self.turnover_day, self.volume_today = t.date(), 0
            # Start-of-session equity uses preceding known prices, before the gap.
            self.risk.update(t, self.equity())
            if kind == "daily":
                rows = daily_snapshots[t]
                regime = regime_map[t]
                b = benchmark_rows.get(t)
            else:
                prior_index = bisect_left(times, t) - 1
                if prior_index < 0:
                    continue
                prior_t = times[prior_index]
                snapshot = prepared.contexts.get(t.date())
                if snapshot is None:
                    snapshot = prepared.daily.iloc[:0].rename(
                        columns={"timestamp": "daily_timestamp", "close": "daily_close"}
                    )
                    # Preserve the feature schema even during liquidity warmup.
                    snapshot = snapshot.merge(
                        dataset.universe[["symbol", "sector", "lot_size"]], on="symbol"
                    )
                keep = [
                    x for x in snapshot.columns if x not in raw.columns or x == "symbol"
                ]
                rows = raw.merge(snapshot[keep], on="symbol", how="inner")
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
            raw_by_symbol = {r.symbol: r for r in raw.itertuples(index=False)}
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
                    open_bar = row._replace(high=row.open, low=row.open)
                    price, reason = protective_fill(position, open_bar)
                    if reason:
                        self.close(symbol, price, t, reason)
                queued, self.pending = self.pending, []
                if queued:
                    date = t.tz_localize(None).normalize()
                    membership = dataset.universe
                    active_symbols = set(
                        membership.loc[
                            (
                                membership.active_from.isna()
                                | (membership.active_from <= date)
                            )
                            & (
                                membership.active_to.isna()
                                | (membership.active_to >= date)
                            ),
                            "symbol",
                        ]
                    )
                new_symbols = set()
                for signal in sorted(
                    queued, key=lambda s: (-s.score, s.strategy, s.symbol)
                ):
                    if signal.symbol not in raw_by_symbol:
                        self.signal_outcome(
                            signal, "expired", "missing_execution_bar", t
                        )
                        continue
                    if signal.symbol not in active_symbols:
                        self.risk.rejections["inactive_membership"] += 1
                        self.signal_outcome(
                            signal, "rejected", "inactive_membership", t
                        )
                        continue
                    if signal.metadata["timeframe"] == "intraday":
                        cutoff = signal.metadata.get("force_exit", "15:10")
                        if (
                            t.date() != signal.timestamp.date()
                            or t.strftime("%H:%M") >= cutoff
                            or t - signal.timestamp > pd.Timedelta(minutes=5)
                        ):
                            self.signal_outcome(
                                signal, "expired", "intraday_cutoff_or_expired", t
                            )
                            continue
                    elif (
                        t.date() <= signal.timestamp.date()
                        or (t.date() - signal.timestamp.date()).days > 7
                    ):
                        self.signal_outcome(signal, "expired", "daily_order_expired", t)
                        continue
                    bar = raw_by_symbol[signal.symbol]
                    raw_fill = entry_fill(signal, bar)
                    if raw_fill is None:
                        self.signal_outcome(
                            signal, "unfilled", "entry_price_not_reached", t
                        )
                        continue
                    price = self.cost.execution_price(raw_fill, signal.side == "LONG")
                    intra = signal.metadata["timeframe"] == "intraday"

                    def fee(qty):
                        return self.cost.fees(price, qty, signal.side == "LONG", intra)

                    before_rejections = self.risk.rejections.copy()
                    order = self.risk.approve(
                        signal,
                        price,
                        self.positions,
                        self.equity(),
                        self.exposure(),
                        fee,
                    )
                    if order is None:
                        reason = next(
                            iter(self.risk.rejections - before_rejections),
                            "risk_rejected",
                        )
                        self.signal_outcome(signal, "rejected", reason, t)
                        continue
                    self.signal_outcome(
                        signal,
                        "filled",
                        "approved",
                        t,
                        fill_price=price,
                        quantity=order.quantity,
                    )
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
            feature_by_symbol = {
                symbol: rows.loc[rows.symbol.eq(symbol)].iloc[0]
                for symbol, position in self.positions.items()
                if self.strategy_map[position.signal.strategy].timeframe == kind
                and rows.symbol.eq(symbol).any()
            }
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
                strategy.last_counts = {}
                signals = (
                    [] if rows.empty else strategy.generate_signals(rows, b, regime, t)
                )
                counts = strategy.last_counts or {
                    "selected": len(signals),
                    "conditions_not_met": len(rows) - len(signals),
                }
                selected_count = counts.get("selected", 0)
                counts["selected"] = len(signals)
                if selected_count > len(signals):
                    counts["invalid_signal_prices"] = selected_count - len(signals)
                if rows.empty:
                    counts["no_eligible_daily_context"] = 1
                for reason, count in counts.items():
                    self.diagnostic_counts[(t.date(), strategy.name, reason)] += count
                for signal in signals:
                    context = rows.loc[rows.symbol.eq(signal.symbol)].iloc[0]
                    signal_id = len(self.signal_records) + 1
                    signal.metadata["signal_id"] = signal_id
                    signal.metadata["screen_timestamp"] = context.get(
                        "daily_timestamp", t
                    )
                    record = {
                        "signal_id": signal_id,
                        "timestamp": t,
                        "screen_timestamp": signal.metadata["screen_timestamp"],
                        "symbol": signal.symbol,
                        "strategy": signal.strategy,
                        "timeframe": strategy.timeframe,
                        "side": signal.side,
                        "order_type": signal.order_type,
                        "entry_price": signal.entry_price,
                        "stop_price": signal.stop_price,
                        "target_price": signal.target_price,
                        "score": signal.score,
                        "regime": regime.label,
                        "status": "pending",
                        "reason": "awaiting_next_bar",
                        "execution_time": pd.NaT,
                        "fill_price": None,
                        "quantity": None,
                        "exit_time": pd.NaT,
                        "exit_reason": None,
                        "net_pnl": None,
                        "features": {
                            **context.to_dict(),
                            **(
                                {
                                    f"trigger_benchmark_{key}": b.get(key)
                                    for key in (
                                        "timestamp",
                                        "close",
                                        "previous_close",
                                        "vwap",
                                    )
                                }
                                if b is not None
                                else {}
                            ),
                        },
                    }
                    self.signal_records.append(record)
                    self.signal_records_by_id[signal_id] = record
                self.pending += signals
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
        if progress:
            progress(
                0.97,
                f"{slippage_bps:g} bps · Closing remaining positions and building screening / signal reports",
            )
        for signal in self.pending:
            self.signal_outcome(signal, "expired", "end_of_data", events[-1][0])
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
        if progress:
            progress(
                1.0,
                f"{slippage_bps:g} bps complete: {len(self.signal_records):,} signals, {len(self.trades):,} closed trades",
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
            prepared.screener,
            pd.DataFrame(
                self.signal_records,
                columns=[
                    "signal_id",
                    "timestamp",
                    "screen_timestamp",
                    "symbol",
                    "strategy",
                    "timeframe",
                    "side",
                    "order_type",
                    "entry_price",
                    "stop_price",
                    "target_price",
                    "score",
                    "regime",
                    "status",
                    "reason",
                    "execution_time",
                    "fill_price",
                    "quantity",
                    "exit_time",
                    "exit_reason",
                    "net_pnl",
                    "features",
                ],
            ),
            pd.DataFrame(
                [
                    {
                        "session": day,
                        "strategy": strategy,
                        "reason": reason,
                        "count": count,
                    }
                    for (day, strategy, reason), count in self.diagnostic_counts.items()
                    if count
                ],
                columns=["session", "strategy", "reason", "count"],
            ),
        )

    def signal_outcome(self, signal, status, reason, timestamp, **values):
        record = self.signal_records_by_id[signal.metadata["signal_id"]]
        record.update(status=status, reason=reason, execution_time=timestamp, **values)

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
                p.signal.timestamp,
                m.get("signal_id", 0),
            )
        )
        record = self.signal_records_by_id[m["signal_id"]]
        record.update(exit_time=timestamp, exit_reason=reason, net_pnl=net)
        self.exit_pending.pop(symbol, None)
