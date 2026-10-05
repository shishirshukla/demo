"""Optional Yahoo daily-data adapter with explicit failures and adjusted OHLC."""

import argparse
import importlib.metadata
import json
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .historical import Dataset
from .validation import TZ, validate_bars, validate_universe

log = logging.getLogger(__name__)


class MissingDependencyError(RuntimeError):
    """An installation error that should not trigger network retries."""


@dataclass
class DownloadResult:
    dataset: Dataset
    manifest: dict


def yahoo_symbol(symbol, suffix=".NS"):
    symbol = str(symbol).strip().upper()
    if not symbol or symbol == "NAN" or any(c.isspace() for c in symbol):
        raise ValueError("Symbols must be non-empty ticker identifiers")
    return symbol if symbol.startswith("^") or "." in symbol else symbol + suffix


def normalize_daily(raw, symbol, ticker=None):
    """Use Yahoo Adj Close / Close on every OHLC column, retaining volume.

    turnover is the provider's unadjusted close times reported volume: a proxy,
    not official exchange traded value. Missing adjustment data is an error.
    """
    if raw is None or raw.empty:
        raise ValueError("Yahoo returned no daily bars")
    raw = raw.copy()
    if isinstance(raw.columns, pd.MultiIndex):
        # Handle both (price,ticker) and (ticker,price) library layouts.
        selected = False
        for level in range(raw.columns.nlevels):
            values = raw.columns.get_level_values(level)
            if ticker is not None and ticker in values:
                raw = raw.xs(ticker, axis=1, level=level)
                selected = True
                break
        if not selected:
            raise ValueError("Cannot identify the requested ticker in Yahoo columns")
    raw.columns = [str(c).strip().lower().replace(" ", "_") for c in raw.columns]
    required = ["open", "high", "low", "close", "adj_close", "volume"]
    if missing := set(required) - set(raw.columns):
        raise ValueError(f"Yahoo response missing columns: {sorted(missing)}")
    if not isinstance(raw.index, pd.DatetimeIndex):
        raise ValueError("Yahoo response must have a datetime index")
    numeric = raw[required].apply(pd.to_numeric, errors="raise")
    # Entirely blank rows are provider placeholders, never invented prices.
    blank = numeric.isna().all(axis=1)
    numeric = numeric.loc[~blank].copy()
    if numeric.empty or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Yahoo returned empty, partial, or non-finite daily bars")
    if (numeric[["open", "high", "low", "close", "adj_close"]] <= 0).any().any():
        raise ValueError("Yahoo returned non-positive prices")
    ratio = numeric.adj_close / numeric.close
    frame = numeric[["open", "high", "low", "close", "volume"]].copy()
    frame["turnover"] = numeric.close * numeric.volume
    for column in ["open", "high", "low", "close"]:
        frame[column] = numeric[column] * ratio
    frame["adjusted_close"] = frame.close
    frame["timestamp"] = numeric.index
    frame["symbol"] = symbol
    frame = validate_bars(frame.reset_index(drop=True))
    frame.attrs["blank_rows_removed"] = int(blank.sum())
    return frame


def _fetch(ticker, **kwargs):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise MissingDependencyError(
            'Install the optional downloader: pip install -e ".[market-data]"'
        ) from exc
    return yf.download(
        ticker,
        interval="1d",
        auto_adjust=False,
        actions=False,
        threads=False,
        progress=False,
        multi_level_index=False,
        ignore_tz=False,
        keepna=True,
        **kwargs,
    )


def download_historical(
    universe,
    start,
    end=None,
    *,
    benchmark="^NSEI",
    suffix=".NS",
    retries=3,
    delay=1.0,
    timeout=30,
    allow_partial=False,
    fetcher=None,
    sleeper=time.sleep,
):
    """Download unique tickers sequentially; partial universes require opt-in."""
    universe = validate_universe(universe)
    if universe.empty or universe.symbol.isna().any():
        raise ValueError("Universe must contain non-empty symbols")
    if "index_group" in universe and universe.index_group.eq("SYNTHETIC").any():
        raise ValueError(
            "The demo universe is fictional. Supply a real universe CSV or --symbols."
        )
    begin = pd.Timestamp(start)
    finish = pd.Timestamp(end or pd.Timestamp.now(TZ).date())
    if (
        pd.isna(begin)
        or pd.isna(finish)
        or begin.tzinfo is not None
        or finish.tzinfo is not None
        or begin != begin.normalize()
        or finish != finish.normalize()
        or begin >= finish
    ):
        raise ValueError("Use date-only start/end with start < end; end is exclusive")
    if (
        retries < 1
        or not np.isfinite(delay)
        or delay < 0
        or not np.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(
            "retries must be positive, delay non-negative, and timeout positive"
        )
    mapping = {}
    for row in universe.itertuples():
        override = getattr(row, "yahoo_symbol", None)
        ticker = (
            str(override).strip()
            if pd.notna(override) and override
            else yahoo_symbol(row.symbol, suffix)
        )
        if row.symbol in mapping and mapping[row.symbol] != ticker:
            raise ValueError(
                "A symbol's membership rows must use the same yahoo_symbol"
            )
        mapping[row.symbol] = ticker
    fetch = fetcher or _fetch
    failures, downloads = {}, {}
    jobs = [("NIFTY50", benchmark, "benchmark")] + [
        (s, t, "stock") for s, t in mapping.items()
    ]
    now = pd.Timestamp.now(TZ)
    for job_index, (symbol, ticker, kind) in enumerate(jobs):
        if job_index and delay:
            sleeper(delay)
        for attempt in range(1, retries + 1):
            try:
                raw = fetch(
                    ticker,
                    start=str(begin.date()),
                    end=str(finish.date()),
                    timeout=timeout,
                )
                frame = normalize_daily(raw, symbol, ticker)
                blanks = frame.attrs.get("blank_rows_removed", 0)
                frame = frame[
                    (frame.timestamp >= begin.tz_localize(TZ))
                    & (frame.timestamp < finish.tz_localize(TZ))
                    & (frame.timestamp < now)
                ].copy()
                if frame.empty:
                    raise ValueError(
                        "No completed daily bars inside the requested range"
                    )
                downloads[(kind, symbol)] = frame
                log.info(
                    "Downloaded %s (%s): %s bars; %s blank rows removed",
                    symbol,
                    ticker,
                    len(frame),
                    blanks,
                )
                break
            except MissingDependencyError:
                # Missing optional dependency is not a retryable provider error.
                raise
            except Exception as exc:
                log.warning(
                    "%s attempt %s/%s failed: %s", ticker, attempt, retries, exc
                )
                if attempt == retries:
                    failures[f"{kind}:{symbol}"] = {
                        "yahoo_symbol": ticker,
                        "error": str(exc),
                    }
                elif delay:
                    sleeper(min(delay * 2 ** (attempt - 1), 30))
    if ("benchmark", "NIFTY50") not in downloads:
        raise ValueError(f"Benchmark download failed; no dataset saved: {failures}")
    if failures and not allow_partial:
        raise ValueError(
            f"Incomplete universe; no dataset saved. Retry or explicitly use --allow-partial: {failures}"
        )
    stocks = [f for (kind, _), f in downloads.items() if kind == "stock"]
    if not stocks:
        raise ValueError("No stocks downloaded; no dataset saved")
    daily = pd.concat(stocks, ignore_index=True)
    successful = set(daily.symbol)
    saved_universe = universe[universe.symbol.isin(successful)].copy()
    dataset = Dataset(
        daily, downloads[("benchmark", "NIFTY50")], saved_universe
    ).validate()
    try:
        version = importlib.metadata.version("yfinance")
    except importlib.metadata.PackageNotFoundError:
        version = "not installed (injected fetcher)"
    manifest = {
        "provider": "Yahoo Finance via yfinance",
        "yfinance_version": version,
        "downloaded_at": now.isoformat(),
        "start_inclusive": str(begin.date()),
        "end_exclusive": str(finish.date()),
        "interval": "1d",
        "timezone": TZ,
        "benchmark": benchmark,
        "symbol_mapping": mapping,
        "successful_symbols": sorted(successful),
        "failures": failures,
        "complete_universe": not failures,
        "price_basis": "All OHLC scaled by Yahoo Adj Close / Close; adjusted_close equals close",
        "volume_basis": "Yahoo-reported volume, retained unchanged",
        "turnover_basis": "Yahoo unadjusted close times volume; approximate traded value",
        "coverage": {
            f"{kind}:{symbol}": {
                "rows": len(f),
                "first": f.timestamp.min().isoformat(),
                "last": f.timestamp.max().isoformat(),
            }
            for (kind, symbol), f in downloads.items()
        },
    }
    return DownloadResult(dataset, manifest)


def save_download(result, output, overwrite=False):
    output = Path(output)
    names = ["daily.csv", "benchmark.csv", "universe.csv", "download_manifest.json"]
    if not overwrite and any((output / name).exists() for name in names):
        raise FileExistsError(
            "Output files already exist; select another folder or pass --overwrite"
        )
    output.mkdir(parents=True, exist_ok=True)
    # Validate before writing and stage every file before replacing destinations.
    result.dataset.validate()
    with tempfile.TemporaryDirectory(dir=output, prefix=".download-") as staging:
        staging = Path(staging)
        result.dataset.daily.to_csv(staging / "daily.csv", index=False)
        result.dataset.benchmark.to_csv(staging / "benchmark.csv", index=False)
        result.dataset.universe.to_csv(staging / "universe.csv", index=False)
        (staging / "download_manifest.json").write_text(
            json.dumps(result.manifest, indent=2, allow_nan=False), encoding="utf-8"
        )
        for name in names:
            (staging / name).replace(output / name)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Download adjusted daily stock and NIFTY 50 CSVs from Yahoo Finance"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--universe", help="Real universe CSV, with optional yahoo_symbol overrides"
    )
    source.add_argument(
        "--symbols",
        nargs="+",
        help="Stock symbols, e.g. RELIANCE TCS INFY (also accepts commas)",
    )
    parser.add_argument(
        "--start", default="2012-01-01", help="Inclusive start date; default 2012-01-01"
    )
    parser.add_argument(
        "--end", help="Exclusive end date; default today in Asia/Kolkata"
    )
    parser.add_argument("--benchmark", default="^NSEI")
    parser.add_argument(
        "--suffix", default=".NS", help="Yahoo exchange suffix for plain stock symbols"
    )
    parser.add_argument("--output", default="data/private/yfinance")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests and retry backoff base, seconds",
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Explicitly allow failed stocks to be excluded and recorded",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        if args.universe:
            universe = pd.read_csv(args.universe)
        else:
            symbols = sorted(
                {
                    symbol.strip().upper()
                    for group in args.symbols
                    for symbol in group.split(",")
                    if symbol.strip()
                }
            )
            if not symbols:
                raise ValueError("Supply at least one symbol")
            universe = pd.DataFrame(
                {
                    "symbol": symbols,
                    "sector": "Unknown",
                    "index_group": "USER_SUPPLIED",
                    "lot_size": 1,
                }
            )
        output = Path(args.output)
        if not args.overwrite and any(
            (output / name).exists()
            for name in [
                "daily.csv",
                "benchmark.csv",
                "universe.csv",
                "download_manifest.json",
            ]
        ):
            raise FileExistsError(
                "Output exists; use a different --output directory or --overwrite"
            )
        result = download_historical(
            universe,
            args.start,
            args.end,
            benchmark=args.benchmark,
            suffix=args.suffix,
            retries=args.retries,
            delay=args.delay,
            timeout=args.timeout,
            allow_partial=args.allow_partial,
        )
        save_download(result, output, args.overwrite)
        if result.manifest["failures"]:
            log.warning("PARTIAL UNIVERSE: %s", result.manifest["failures"])
        print(
            f"Saved {len(result.dataset.daily):,} stock bars for {len(result.manifest['successful_symbols'])} symbols to {output.resolve()}"
        )
        print(
            f'Backtest: python -m trading_system.main --daily "{output / "daily.csv"}" --benchmark "{output / "benchmark.csv"}" --universe "{output / "universe.csv"}"'
        )
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        log.error("Download failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
