"""Load already generated daily screening and five-minute execution candles."""

import json
from pathlib import Path

import pandas as pd

from .historical import Dataset
from .validation import validate_bars

CONVERTED_ROOT = Path(__file__).resolve().parents[2] / "data/private/kite_converted"


def converted_catalog(root=CONVERTED_ROOT):
    entries = []
    for path in sorted(Path(root).glob("*/conversion_manifest.json"), reverse=True):
        try:
            manifest = json.loads(path.read_text())
            coverage = manifest["coverage"]
            if manifest.get("format_version") != 1:
                continue
            if any(Path(r["filename"]).name != r["filename"] for r in coverage):
                continue
            entries.append(
                {
                    "path": path.parent,
                    "manifest": manifest,
                    "symbols": [r["symbol"] for r in coverage],
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


def converted_fingerprint(folder, symbols, mode):
    folder = Path(folder)
    manifest = json.loads((folder / "conversion_manifest.json").read_text())
    paths = [folder / "conversion_manifest.json"]
    for row in manifest["coverage"]:
        if row["symbol"] in symbols:
            filename = row["filename"]
            if Path(filename).name != filename:
                raise ValueError("Invalid converted filename")
            paths += [folder / "daily" / filename]
            if mode != "daily":
                paths += [folder / "5minute" / filename]
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def load_converted_dataset(
    folder,
    symbols,
    benchmark="NIFTY 50",
    *,
    mode="hybrid",
    universe=None,
    progress=None,
):
    folder = Path(folder)
    manifest = json.loads((folder / "conversion_manifest.json").read_text())
    if (
        manifest.get("format_version") != 1
        or manifest.get("timezone") != "Asia/Kolkata"
    ):
        raise ValueError("Unsupported converted dataset format or timezone")
    symbols = list(dict.fromkeys(symbols))
    if not symbols or benchmark in symbols:
        raise ValueError("Select stocks separately from the benchmark")
    required = [*symbols, benchmark]
    catalog = {r["symbol"]: r for r in manifest["coverage"]}
    if missing := set(required) - catalog.keys():
        raise ValueError(f"Missing converted symbols: {sorted(missing)}")
    daily, five, coverage = [], [], []
    for i, symbol in enumerate(required):
        if progress:
            progress(
                i / len(required) * 0.95,
                f"Loading {symbol} ({i + 1}/{len(required)}): daily candles and 5-minute execution bars",
            )
        filename = catalog[symbol]["filename"]
        if Path(filename).name != filename:
            raise ValueError("Invalid converted filename")
        d = validate_bars(pd.read_csv(folder / "daily" / filename))
        if set(d.symbol) != {symbol}:
            raise ValueError(f"Daily file contains unexpected symbols: {symbol}")
        daily.append(d)
        excluded = 0
        if mode != "daily":
            f = validate_bars(pd.read_csv(folder / "5minute" / filename), True)
            if set(f.symbol) != {symbol}:
                raise ValueError(f"5-minute file contains unexpected symbols: {symbol}")
            sessions = f.timestamp.dt.normalize()
            counts = f.groupby(sessions).size()
            complete = set(counts[counts.eq(75)].index) & set(
                d.timestamp.dt.normalize()
            )
            excluded = int((~counts.index.isin(complete)).sum())
            five.append(f.loc[sessions.isin(complete)])
        coverage.append(
            {
                "symbol": symbol,
                "daily_sessions": len(d),
                "warmup_200_ready": len(d) >= 200,
                "excluded_execution_sessions": excluded,
            }
        )
    b = daily[-1]
    benchmark_dates = set(b.timestamp)
    stocks = pd.concat(daily[:-1], ignore_index=True)
    stocks = stocks.loc[stocks.timestamp.isin(benchmark_dates)]
    if stocks.empty:
        raise ValueError("No stock sessions with daily benchmark coverage")
    stock_five = benchmark_five = None
    if five:
        stock_five = pd.concat(five[:-1], ignore_index=True)
        stock_five = stock_five.loc[
            stock_five.timestamp.dt.normalize().isin(b.timestamp.dt.normalize())
        ]
        benchmark_five = five[-1]
        if stock_five.empty:
            raise ValueError("No complete 5-minute stock sessions for execution")
    if universe is None:
        universe = pd.DataFrame({"symbol": symbols, "sector": "Unknown"})
    if progress:
        progress(0.96, "Validating daily screening and complete 5-minute sessions")
    dataset = Dataset(stocks, b, universe, stock_five, benchmark_five).validate()
    summary = {
        "available_sessions": stocks.timestamp.nunique(),
        "first_session": str(stocks.timestamp.min().date()),
        "last_session": str(stocks.timestamp.max().date()),
        "per_symbol": coverage,
        "session_policy": "Independent complete sessions per stock; daily history preserved for warmup; no missing bars filled",
    }
    if progress:
        progress(
            1.0,
            f"Ready: {len(symbols)} stocks, {len(stocks):,} daily candles, {0 if stock_five is None else len(stock_five):,} 5-minute bars",
        )
    return dataset, summary
