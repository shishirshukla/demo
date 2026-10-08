"""Reusable causal screening and execution features for all cost scenarios."""

from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from trading_system.data.features import (
    daily_features,
    eligible_rows,
    intraday_features,
)
from trading_system.regime import RegimeClassifier
from trading_system.strategies import build_strategies
from trading_system.strategies.base import BaseStrategy


@dataclass
class PreparedBacktest:
    daily: pd.DataFrame
    bars: pd.DataFrame | None
    snapshots: dict
    regimes: dict
    benchmark_rows: dict
    intraday_benchmark: dict
    contexts: dict
    events: list
    screener: pd.DataFrame
    start: object
    end: object
    mode: str
    settings: dict
    dataset_id: int


def prepare_backtest(
    dataset, settings, start=None, end=None, mode="hybrid", progress=None
):
    def report(fraction, message):
        if progress:
            progress(fraction, message)

    if mode not in ("daily", "intraday", "hybrid"):
        raise ValueError("mode must be daily, intraday, or hybrid")
    report(0.0, "Validating daily candles, universe and 5-minute execution data")
    dataset.validate()
    if mode != "daily" and dataset.intraday is None:
        raise ValueError("Intraday/hybrid mode requires 5-minute bars")
    classifier = RegimeClassifier(settings["regime"])
    report(0.04, "Preparing benchmark trend, ADX and relative-strength history")
    benchmark = classifier.prepare(dataset.benchmark)
    daily = daily_features(
        dataset.daily, benchmark, settings, lambda f, m: report(0.06 + f * 0.16, m)
    )
    groups = daily.groupby("timestamp")
    benchmark_rows = {r.timestamp: r for _, r in benchmark.iterrows()}
    screening_settings = deepcopy(settings)
    for params in screening_settings["strategies"].values():
        params["enabled"] = True
    strategies = build_strategies(screening_settings)
    snapshots, regimes, screening = {}, {}, []
    for i, (t, frame) in enumerate(groups):
        if i % 20 == 0:
            report(
                0.22 + 0.16 * i / len(groups),
                f"Daily screening: {t.date()} ({i + 1}/{len(groups)} sessions)",
            )
        eligible = eligible_rows(frame, dataset.universe, t, settings)
        snapshots[t] = eligible
        regime = regimes[t] = classifier.classify(benchmark_rows.get(t), eligible)
        full = frame.merge(
            eligible[
                [
                    "symbol",
                    "sector",
                    "lot_size",
                    "rs20_rank",
                    "rs60_rank",
                    "vol_rank",
                    "atr_rank",
                    "volatility_quartile",
                    "liquidity_quartile",
                ]
            ],
            on="symbol",
            how="left",
        )
        # Display sector metadata even when a stock fails liquidity screening.
        sectors = dataset.universe.drop_duplicates("symbol").set_index("symbol").sector
        full["sector"] = full.sector.fillna(full.symbol.map(sectors))
        date = t.tz_localize(None).normalize()
        active = dataset.universe.loc[
            (
                dataset.universe.active_from.isna()
                | (dataset.universe.active_from <= date)
            )
            & (
                dataset.universe.active_to.isna() | (dataset.universe.active_to >= date)
            ),
            "symbol",
        ]
        checks = {
            "inactive_membership": full.symbol.isin(active),
            "liquidity_warmup": full.avg_turnover.notna() & full.avg_volume.notna(),
            "price_below_minimum": full.close >= settings["liquidity"]["min_price"],
            "turnover_below_minimum": full.avg_turnover
            >= settings["liquidity"]["min_turnover"],
            "volume_below_minimum": full.avg_volume
            >= settings["liquidity"]["min_volume"],
        }
        full["eligibility_reason"] = BaseStrategy.reasons(full, checks)
        full["eligible"] = full.eligibility_reason.eq("selected")
        full["regime"], full["breadth"], full["adx"] = (
            regime.label,
            regime.breadth,
            regime.adx,
        )
        for strategy in strategies:
            reasons = strategy.reasons(full, strategy.daily_checks(full, regime))
            inactive_mode = (mode == "daily" and strategy.timeframe == "intraday") or (
                mode == "intraday" and strategy.timeframe == "daily"
            )
            if not settings["strategies"][strategy.name]["enabled"]:
                reasons[:] = "strategy_disabled"
            elif inactive_mode:
                reasons[:] = "timeframe_disabled"
            elif (
                strategy.name == "opening_momentum"
                and dataset.benchmark_intraday is None
            ):
                reasons[:] = "missing_intraday_benchmark"
            full[strategy.name + "_reason"] = reasons.where(
                full.eligible, full.eligibility_reason
            )
            full[strategy.name + "_selected"] = full[strategy.name + "_reason"].eq(
                "selected"
            )
        screening.append(full)
    screener = (
        pd.concat(screening, ignore_index=True)
        .sort_values(["timestamp", "symbol"])
        .reset_index(drop=True)
    )
    screener["daily_history_sessions"] = screener.groupby("symbol").cumcount() + 1
    contexts, intraday_benchmark, bars = {}, {}, None
    events = [
        (t, 1, "daily", indices)
        for t, indices in daily.groupby("timestamp").indices.items()
    ]
    if mode != "daily":
        bars = intraday_features(
            dataset.intraday, settings, lambda f, m: report(0.38 + f * 0.32, m)
        )
        counts = bars.groupby(["symbol", "session"]).size()
        if not counts.eq(75).all():
            raise ValueError(
                "Intraday research requires complete 09:20–15:30 close-timestamp sessions per symbol (75 bars)"
            )
        report(
            0.71, "Joining strictly prior daily screening context to execution sessions"
        )
        left = bars[["symbol", "session"]].drop_duplicates().copy()
        left["context_time"] = (
            pd.to_datetime(left.session)
            .dt.tz_localize("Asia/Kolkata")
            .astype(screener.timestamp.dtype)
        )
        context = pd.merge_asof(
            left.sort_values("context_time"),
            screener.sort_values("timestamp"),
            left_on="context_time",
            right_on="timestamp",
            by="symbol",
            allow_exact_matches=False,
            tolerance=pd.Timedelta(days=7),
        )
        context = context.loc[context.eligible.eq(True)].rename(
            columns={
                "timestamp": "daily_timestamp",
                "close": "daily_close",
                "open": "daily_open",
                "high": "daily_high",
                "low": "daily_low",
                "volume": "daily_volume",
            }
        )
        contexts = {
            session: f.drop(columns=["session", "context_time"])
            for session, f in context.groupby("session")
        }
        events += [
            (t, 0, "intraday", indices)
            for t, indices in bars.groupby("timestamp").indices.items()
        ]
        if dataset.benchmark_intraday is not None:
            bf = intraday_features(
                dataset.benchmark_intraday,
                settings,
                lambda f, m: report(0.76 + f * 0.08, "Benchmark " + m),
            )
            bf["timestamp"] = bf.timestamp.astype(benchmark.timestamp.dtype)
            prior = pd.merge_asof(
                bf[["timestamp"]].sort_values("timestamp"),
                benchmark[["timestamp", "close"]].rename(
                    columns={"close": "previous_close"}
                ),
                on="timestamp",
                allow_exact_matches=False,
                tolerance=pd.Timedelta(days=7),
            )
            bf["previous_close"] = prior.previous_close.to_numpy()
            intraday_benchmark = {r.timestamp: r for _, r in bf.iterrows()}
        first_exec, last_exec = bars.timestamp.min(), bars.timestamp.max()
    else:
        first_exec, last_exec = daily.timestamp.min(), daily.timestamp.max()
    lower = pd.Timestamp(start, tz="Asia/Kolkata") if start else first_exec.normalize()
    upper = (
        pd.Timestamp(end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
        if end
        else last_exec + pd.Timedelta(seconds=1)
    )
    events = sorted(
        [
            e
            for e in events
            if lower <= e[0] < upper
            and (mode == "daily" or first_exec.normalize() <= e[0] <= last_exec)
        ],
        key=lambda e: (e[0], e[1]),
    )
    if not events:
        raise ValueError("No tradable data in requested date range")
    # Warmup history remains inspectable; execution is restricted by events above.
    screener = screener.loc[screener.timestamp < upper].reset_index(drop=True)
    report(
        1.0,
        f"Prepared {len(screener):,} daily screening rows and {len(events):,} execution/screening events",
    )
    return PreparedBacktest(
        daily,
        bars,
        snapshots,
        regimes,
        benchmark_rows,
        intraday_benchmark,
        contexts,
        events,
        screener,
        start,
        end,
        mode,
        deepcopy(settings),
        id(dataset),
    )
