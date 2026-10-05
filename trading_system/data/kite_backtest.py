"""Convert locally saved Kite minute datasets to research data contracts."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .historical import Dataset

COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
TZ = "Asia/Kolkata"


def saved_kite_datasets(root):
    entries = []
    for path in sorted(Path(root).glob("*/download_manifest.json"), reverse=True):
        if path.parent.name.startswith("."):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if (
                manifest.get("provider") != "Zerodha Kite Connect"
                or not (path.parent / "candles_1minute.csv").is_file()
            ):
                continue
            symbols = [row["symbol"] for row in manifest.get("coverage", [])]
            entries.append(
                {"path": path.parent, "symbols": symbols, "manifest": manifest}
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


def load_kite_candles(paths):
    frames, masters = [], []
    for folder in paths:
        folder = Path(folder)
        manifest = json.loads(
            (folder / "download_manifest.json").read_text(encoding="utf-8")
        )
        if (
            manifest.get("provider") != "Zerodha Kite Connect"
            or manifest.get("interval") != "minute"
            or manifest.get("timestamp_convention") != "bar start (Kite native)"
        ):
            raise ValueError(f"{folder.name}: expected Kite one-minute bar-start data")
        frame = pd.read_csv(folder / "candles_1minute.csv")
        if not set(COLUMNS).issubset(frame.columns):
            raise ValueError(f"{folder.name}: missing minute OHLCV columns")
        frame = frame[COLUMNS].copy()
        frame["timestamp"] = pd.to_datetime(
            frame.timestamp, utc=True, errors="raise"
        ).dt.tz_convert(TZ)
        for column in COLUMNS[2:]:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frames.append(frame)
        masters.append(pd.read_csv(folder / "instruments.csv"))
    if not frames:
        raise ValueError("Select at least one saved dataset")
    frame = pd.concat(frames, ignore_index=True).drop_duplicates()
    if frame.duplicated(["timestamp", "symbol"]).any():
        raise ValueError(
            "Selected downloads contain conflicting candles for the same symbol and minute. Select non-conflicting datasets."
        )
    if frame.timestamp.isna().any() or frame.symbol.isna().any():
        raise ValueError("Saved candles contain missing symbols or timestamps")
    if (frame.timestamp != frame.timestamp.dt.floor("min")).any():
        raise ValueError("Saved candles must have minute-aligned timestamps")
    numeric = frame[COLUMNS[2:]]
    if (
        not np.isfinite(numeric).all().all()
        or (numeric.iloc[:, :4] <= 0).any().any()
        or (frame.volume < 0).any()
    ):
        raise ValueError("Saved candles contain invalid prices or volume")
    if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any() or (
        frame.low > frame[["open", "high", "close"]].min(axis=1)
    ).any():
        raise ValueError("Saved candles contain invalid OHLC ranges")
    return frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True), pd.concat(
        masters, ignore_index=True
    ).drop_duplicates("tradingsymbol", keep="first")


def prepare_kite_backtest(
    candles, symbols, benchmark_symbol, *, mode="daily", universe=None
):
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise ValueError("Select at least one downloaded stock")
    if benchmark_symbol in symbols:
        raise ValueError("The benchmark cannot also be selected as a stock")
    required = set(symbols) | {benchmark_symbol}
    if missing := required - set(candles.symbol):
        raise ValueError(f"Missing downloaded candles: {sorted(missing)}")
    frame = candles.loc[candles.symbol.isin(required)].copy()
    minute = frame.timestamp.dt.hour * 60 + frame.timestamp.dt.minute
    frame = frame.loc[(minute >= 555) & (minute < 930)].copy()
    frame["session"] = frame.timestamp.dt.normalize()
    complete, excluded = {}, []
    for (symbol, session), bars in frame.groupby(["symbol", "session"]):
        expected = pd.date_range(
            session + pd.Timedelta(hours=9, minutes=15), periods=375, freq="min"
        )
        if len(bars) == 375 and set(bars.timestamp) == set(expected):
            complete.setdefault(symbol, set()).add(session)
        else:
            excluded.append(
                {"symbol": symbol, "session": str(session.date()), "minutes": len(bars)}
            )
    common = set.intersection(*(complete.get(symbol, set()) for symbol in required))
    if not common:
        raise ValueError(
            "No common complete sessions for the selected stocks and benchmark. Download overlapping full sessions with all 375 regular-market minutes (09:15-15:29)."
        )
    frame = frame.loc[frame.session.isin(common)].sort_values("timestamp")
    daily = frame.groupby(["symbol", "session"], as_index=False).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    daily["timestamp"] = daily.pop("session") + pd.Timedelta(hours=15, minutes=30)
    daily["turnover"] = (
        (frame.close * frame.volume)
        .groupby([frame.symbol, frame.session])
        .sum()
        .to_numpy()
    )
    five = None
    if mode != "daily":
        frame["timestamp"] = frame.timestamp.dt.floor("5min") + pd.Timedelta(minutes=5)
        five = frame.groupby(["symbol", "timestamp"], as_index=False).agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
    if universe is None:
        universe = pd.DataFrame({"symbol": symbols, "sector": "Unknown", "lot_size": 1})
    universe = universe.loc[universe.symbol.isin(symbols)].copy()
    dataset = Dataset(
        daily.loc[daily.symbol.isin(symbols), COLUMNS + ["turnover"]],
        daily.loc[daily.symbol == benchmark_symbol, COLUMNS],
        universe,
        five.loc[five.symbol.isin(symbols), COLUMNS] if five is not None else None,
        five.loc[five.symbol == benchmark_symbol, COLUMNS]
        if five is not None
        else None,
    ).validate()
    coverage = {
        "common_sessions": len(common),
        "first_session": str(min(common).date()),
        "last_session": str(max(common).date()),
        "excluded_incomplete_sessions": excluded,
        "complete_sessions_outside_common_range": sum(
            len(dates - common) for dates in complete.values()
        ),
    }
    return dataset, coverage
