"""Causal feature construction; intraday joins strictly prior daily sessions."""

import numpy as np
import pandas as pd

from trading_system.indicators import atr, ema, percentile, rsi, sma, vwap

from .corporate_actions import check_adjustments


def daily_features(data, benchmark, settings):
    check_adjustments(data)
    check_adjustments(benchmark)
    pieces = []
    for _, group in data.groupby("symbol", sort=True):
        f = group.sort_values("timestamp").copy()
        for n in [20, 50, 200]:
            f[f"sma{n}"] = sma(f.close, n)
        f["ema20"] = ema(f.close, 20)
        f["atr"] = atr(f)
        f["atr_fraction"] = f.atr / f.close
        f["volatility"] = f.close.pct_change().rolling(20).std()
        for n in [10, 20]:
            f[f"prior_high{n}"] = f.high.rolling(n).max().shift()
            f[f"prior_low{n}"] = f.low.rolling(n).min().shift()
        breakout = settings["strategies"]["volatility_breakout"]["breakout_lookback"]
        support = settings["strategies"]["weakness_breakdown"]["support_lookback"]
        f["breakout_high"] = f.high.rolling(breakout).max().shift()
        f["support"] = f.low.rolling(support).min().shift()
        f["swing_low"] = f.low.rolling(5).min()
        f["previous_high"] = f.high.shift()
        f["previous_close"] = f.close.shift()
        f["avg_volume"] = f.volume.rolling(20).mean().shift()
        value = f.turnover if "turnover" in f else f.close * f.volume
        f["avg_turnover"] = value.rolling(20).mean()
        for n in [20, 60]:
            f[f"return{n}"] = f.close.pct_change(n)
        # Compression is assessed on the PRE-breakout session, cross-sectionally.
        f["pre_volatility"] = f.volatility.shift()
        f["pre_atr_fraction"] = f.atr_fraction.shift()
        pieces.append(f)
    f = pd.concat(pieces, ignore_index=True)
    b = benchmark[["timestamp", "return20", "return60"]].rename(
        columns={"return20": "benchmark20", "return60": "benchmark60"}
    )
    f = f.merge(b, on="timestamp", how="left", validate="many_to_one")
    for n in [20, 60]:
        f[f"rs{n}"] = f[f"return{n}"] - f[f"benchmark{n}"]
    return f.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def eligible_rows(frame, universe, timestamp, settings):
    date = pd.Timestamp(timestamp).tz_localize(None).normalize()
    active = universe[
        (universe.active_from.isna() | (universe.active_from <= date))
        & (universe.active_to.isna() | (universe.active_to >= date))
    ]
    f = frame.drop(columns=["sector", "lot_size"], errors="ignore").merge(
        active[["symbol", "sector", "lot_size"]],
        on="symbol",
        how="inner",
        validate="many_to_one",
    )
    c = settings["liquidity"]
    f = f[
        (f.close >= c["min_price"])
        & (f.avg_turnover >= c["min_turnover"])
        & (f.avg_volume >= c["min_volume"])
    ].copy()
    for name, source in [
        ("rs20_rank", "rs20"),
        ("rs60_rank", "rs60"),
        ("vol_rank", "pre_volatility"),
        ("atr_rank", "pre_atr_fraction"),
    ]:
        f[name] = percentile(f[source])
    f["volatility_quartile"] = (
        np.ceil(percentile(f.volatility) * 4).fillna(0).astype(int)
    )
    f["liquidity_quartile"] = (
        np.ceil(percentile(f.avg_turnover) * 4).fillna(0).astype(int)
    )
    return f


def intraday_features(data, settings):
    pieces = []
    for _, group in data.groupby("symbol", sort=True):
        f = group.sort_values("timestamp").copy().reset_index(drop=True)
        f["session"] = f.timestamp.dt.date
        f["minute"] = f.timestamp.dt.hour * 60 + f.timestamp.dt.minute
        f["vwap"] = vwap(f)
        f["atr_intraday"] = atr(f)
        f["rsi5"] = f.groupby("session").close.transform(lambda s: rsi(s, 5))
        f["std"] = f.groupby("session").close.transform(
            lambda s: s.rolling(12, min_periods=6).std()
        )
        for column in ["high", "low"]:
            f[f"previous_{column}_intraday"] = f.groupby("session")[column].shift()
            f[f"recent_{column}"] = f.groupby("session")[column].transform(
                lambda s: (
                    s.rolling(5, min_periods=3).max()
                    if column == "high"
                    else s.rolling(5, min_periods=3).min()
                )
            )
        f["cumulative_volume"] = f.groupby("session").volume.cumsum()
        baseline = f.groupby("minute").cumulative_volume.transform(
            lambda s: s.shift().rolling(20, min_periods=5).mean()
        )
        f["relative_volume"] = f.cumulative_volume / baseline.replace(0, np.nan)
        minutes = settings["strategies"]["opening_momentum"]["opening_range_minutes"]
        end = 555 + minutes
        opening = (
            f[f.minute <= end]
            .groupby("session")
            .agg(or_high=("high", "max"), or_low=("low", "min"))
        )
        f = f.join(opening, on="session")
        f.loc[f.minute <= end, ["or_high", "or_low"]] = np.nan
        f["session_open"] = f.groupby("session").open.transform("first")
        f["session_return"] = f.close / f.session_open - 1
        pieces.append(f)
    return (
        pd.concat(pieces, ignore_index=True)
        .sort_values(["timestamp", "symbol"])
        .reset_index(drop=True)
    )
