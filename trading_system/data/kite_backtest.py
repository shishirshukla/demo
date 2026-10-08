"""Convert locally saved Kite minute datasets to research data contracts."""

import json
from contextlib import closing
from pathlib import Path

import pandas as pd

from .historical import Dataset
from .minute_candles import COLUMNS, iter_minute_candles
from .minute_candles import deduplicate_minute_candles as _deduplicate_candles
from .minute_candles import normalize_minute_candles as _normalize_candles
from .minute_candles import validate_kite_manifest as _validate_manifest


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
        _validate_manifest(folder)
        frames.append(_normalize_candles(pd.read_csv(folder / "candles_1minute.csv")))
        masters.append(pd.read_csv(folder / "instruments.csv"))
    if not frames:
        raise ValueError("Select at least one saved dataset")
    frame = _deduplicate_candles(pd.concat(frames, ignore_index=True))
    return frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True), pd.concat(
        masters, ignore_index=True
    ).drop_duplicates("tradingsymbol", keep="first")


def _selected_symbols(symbols, benchmark_symbol):
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise ValueError("Select at least one downloaded stock")
    if benchmark_symbol in symbols:
        raise ValueError("The benchmark cannot also be selected as a stock")
    return symbols


def aggregate_complete_sessions(frame, mode="daily"):
    """Aggregate validated minute starts, retaining only full regular sessions."""
    minute = frame.timestamp.dt.hour * 60 + frame.timestamp.dt.minute
    frame = frame.loc[(minute >= 555) & (minute < 930)].copy()
    frame["session"] = frame.timestamp.dt.normalize()
    frame["aligned"] = frame.timestamp.eq(frame.timestamp.dt.floor("min"))
    sessions = frame.groupby(["symbol", "session"]).agg(
        minutes=("timestamp", "size"),
        unique_minutes=("timestamp", "nunique"),
        aligned=("aligned", "all"),
    )
    # There are exactly 375 distinct, aligned minute starts in 09:15-15:29.
    # Counts prove completeness without constructing a timestamp set per session.
    sessions["complete"] = (
        sessions.minutes.eq(375) & sessions.unique_minutes.eq(375) & sessions.aligned
    )
    complete, excluded = {}, []
    for row in sessions.reset_index().itertuples(index=False):
        symbol, session = row.symbol, row.session
        if row.complete:
            complete.setdefault(symbol, set()).add(session)
        else:
            excluded.append(
                {
                    "symbol": symbol,
                    "session": str(session.date()),
                    "minutes": row.minutes,
                }
            )
    valid = sessions.index[sessions.complete]
    frame = frame.loc[
        pd.MultiIndex.from_frame(frame[["symbol", "session"]]).isin(valid)
    ].sort_values("timestamp")
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
    return daily, five, complete, excluded


def _assemble_backtest(daily, five, complete, excluded, symbols, benchmark, universe):
    required = set(symbols) | {benchmark}
    common = set.intersection(*(complete.get(symbol, set()) for symbol in required))
    benchmark_dates = complete.get(benchmark, set())
    available = set.union(*(complete.get(s, set()) for s in symbols)) & benchmark_dates
    if not available:
        raise ValueError(
            "No common complete sessions between any selected stock and the benchmark. Download overlapping full sessions with all 375 regular-market minutes (09:15-15:29)."
        )
    # A recent listing or a missing minute in ONE stock must not erase the
    # other stocks' history (including their indicator warmup).
    daily = daily.loc[daily.timestamp.dt.normalize().isin(benchmark_dates)]
    if five is not None:
        five = five.loc[five.timestamp.dt.normalize().isin(benchmark_dates)]
    if universe is None:
        universe = pd.DataFrame({"symbol": symbols, "sector": "Unknown", "lot_size": 1})
    universe = universe.loc[universe.symbol.isin(symbols)].copy()
    dataset = Dataset(
        daily.loc[daily.symbol.isin(symbols), COLUMNS + ["turnover"]],
        daily.loc[daily.symbol == benchmark, COLUMNS],
        universe,
        five.loc[five.symbol.isin(symbols), COLUMNS] if five is not None else None,
        five.loc[five.symbol == benchmark, COLUMNS] if five is not None else None,
    ).validate()
    coverage = {
        "common_sessions": len(common),
        "available_sessions": len(available),
        "session_policy": "Independent complete stock sessions with benchmark coverage; no missing bars filled",
        "first_session": str(min(available).date()),
        "last_session": str(max(available).date()),
        "excluded_incomplete_sessions": sorted(
            excluded, key=lambda row: (row["symbol"], row["session"])
        ),
        "complete_sessions_outside_common_range": sum(
            len(dates - common) for dates in complete.values()
        ),
        "per_symbol": [
            {
                "symbol": s,
                "daily_sessions": len(complete.get(s, set()) & benchmark_dates),
                "warmup_200_ready": len(complete.get(s, set()) & benchmark_dates)
                >= 200,
            }
            for s in symbols
        ],
    }
    return dataset, coverage


def prepare_kite_backtest(
    candles, symbols, benchmark_symbol, *, mode="daily", universe=None
):
    symbols = _selected_symbols(symbols, benchmark_symbol)
    required = set(symbols) | {benchmark_symbol}
    if missing := required - set(candles.symbol):
        raise ValueError(f"Missing downloaded candles: {sorted(missing)}")
    aggregates = aggregate_complete_sessions(
        candles.loc[candles.symbol.isin(required)], mode
    )
    return _assemble_backtest(*aggregates, symbols, benchmark_symbol, universe)


def prepare_saved_kite_backtest(
    paths,
    symbols,
    benchmark_symbol,
    *,
    mode="daily",
    universe=None,
    progress=None,
    chunksize=100_000,
):
    """Scan CSVs in bounded chunks and aggregate one instrument at a time.

    Temporary partitions allow arbitrary CSV ordering and exact deduplication
    across chunks/downloads without retaining the whole minute universe in RAM.
    Only the requested stocks and benchmark are partitioned and validated.
    """
    symbols = _selected_symbols(symbols, benchmark_symbol)
    required = [*symbols, benchmark_symbol]
    folders = list(dict.fromkeys(Path(path).resolve() for path in paths))
    if not folders:
        raise ValueError("Select at least one saved dataset")
    for folder in folders:
        _validate_manifest(folder)
    daily_parts, five_parts, complete, excluded = [], [], {}, []
    with closing(
        iter_minute_candles(
            [folder / "candles_1minute.csv" for folder in folders],
            symbols=required,
            progress=progress,
            chunksize=chunksize,
        )
    ) as stream:
        for _, candles in stream:
            daily, five, dates, incomplete = aggregate_complete_sessions(candles, mode)
            daily_parts.append(daily)
            if five is not None:
                five_parts.append(five)
            complete.update(dates)
            excluded.extend(incomplete)
    if progress:
        progress(1.0, "Validating prepared backtest data...")
    return _assemble_backtest(
        pd.concat(daily_parts, ignore_index=True),
        pd.concat(five_parts, ignore_index=True) if five_parts else None,
        complete,
        excluded,
        symbols,
        benchmark_symbol,
        universe,
    )
