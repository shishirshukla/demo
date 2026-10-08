"""Export per-symbol five-minute and daily candles from local minute data."""

import argparse
import json
import re
import shutil
import tempfile
from contextlib import closing
from pathlib import Path

import pandas as pd

from .kite_backtest import aggregate_complete_sessions, saved_kite_datasets
from .kite_downloader import STORAGE_ROOT
from .minute_candles import COLUMNS, TZ, iter_minute_candles, validate_kite_manifest

OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "data/private/kite_converted"


def _five_minute_candles(candles):
    frame = candles.copy()
    frame["timestamp"] = frame.timestamp.dt.floor("5min") + pd.Timedelta(minutes=5)
    five = frame.groupby(["symbol", "timestamp"], as_index=False).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        minutes=("timestamp", "size"),
    )
    incomplete = [
        {"timestamp": row.timestamp.isoformat(), "minutes": row.minutes}
        for row in five.loc[five.minutes.ne(5)].itertuples(index=False)
    ]
    return five.loc[five.minutes.eq(5), COLUMNS], incomplete


def _sources(inputs):
    files = []
    for source in inputs:
        source = Path(source).resolve()
        if source.is_dir():
            if (source / "download_manifest.json").exists():
                validate_kite_manifest(source)
            source = source / "candles_1minute.csv"
        if not source.is_file():
            raise ValueError(f"Minute CSV does not exist: {source}")
        files.append(source)
    if not files:
        raise ValueError("Select at least one minute CSV or saved download folder")
    return list(dict.fromkeys(files))


def convert_kite_minutes(
    inputs,
    output,
    *,
    symbols=None,
    include_minute=False,
    chunksize=100_000,
    progress=None,
):
    """Publish separate symbol CSVs atomically; never overwrite an existing output.

    Five-minute bins require all five minutes. Daily candles require all 375
    regular-session minutes. Each symbol retains its own available dates.
    """
    files = _sources(inputs)
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError(
            f"Output already exists; choose a new directory: {destination}"
        )
    source_stats = [
        {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in files
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".conversion_", dir=destination.parent))
    coverage, filenames = [], set()
    try:
        for name in ["5minute", "daily", *(["1minute"] if include_minute else [])]:
            (staging / name).mkdir()
        with closing(
            iter_minute_candles(
                files,
                symbols=symbols,
                chunksize=chunksize,
                progress=progress,
            )
        ) as stream:
            for symbol, candles in stream:
                filename = re.sub(r"[^A-Za-z0-9_.-]", "_", symbol) + ".csv"
                if filename.casefold() in filenames:
                    raise ValueError(
                        f"Symbols map to the same output filename: {filename}"
                    )
                filenames.add(filename.casefold())
                minute = candles.timestamp.dt.hour * 60 + candles.timestamp.dt.minute
                regular = candles.loc[(minute >= 555) & (minute < 930)].copy()
                daily, _, _, incomplete_sessions = aggregate_complete_sessions(regular)
                five, incomplete_bins = _five_minute_candles(regular)
                five.to_csv(staging / "5minute" / filename, index=False)
                daily[COLUMNS + ["turnover"]].to_csv(
                    staging / "daily" / filename, index=False
                )
                if include_minute:
                    regular[COLUMNS].to_csv(staging / "1minute" / filename, index=False)
                coverage.append(
                    {
                        "symbol": symbol,
                        "filename": filename,
                        "source_minute_rows": len(candles),
                        "regular_minute_rows": len(regular),
                        "excluded_out_of_session_rows": len(candles) - len(regular),
                        "five_minute_rows": len(five),
                        "daily_rows": len(daily),
                        "first_minute": regular.timestamp.iloc[0].isoformat()
                        if len(regular)
                        else None,
                        "last_minute": regular.timestamp.iloc[-1].isoformat()
                        if len(regular)
                        else None,
                        "incomplete_five_minute_bins": incomplete_bins,
                        "excluded_incomplete_sessions": incomplete_sessions,
                    }
                )
        for source in source_stats:
            stat = Path(source["path"]).stat()
            if (stat.st_size, stat.st_mtime_ns) != (
                source["size_bytes"],
                source["mtime_ns"],
            ):
                raise ValueError(
                    "Input files changed during conversion; retry with stable source files"
                )
        manifest = {
            "format_version": 1,
            "timezone": TZ,
            "converted_at": pd.Timestamp.now(tz=TZ).isoformat(),
            "sources": source_stats,
            "input_timestamp_convention": "one-minute bar start; naive timestamps interpreted in Asia/Kolkata",
            "five_minute_timestamp_convention": "bar close: 09:20 through 15:30 IST",
            "daily_timestamp_convention": "session close: 15:30 IST",
            "session_policy": "observed regular-market minutes 09:15-15:29; no missing bars filled",
            "five_minute_policy": "retain only bins with all five source minutes",
            "daily_policy": "retain only sessions with all 375 source minutes, independently per symbol",
            "duplicate_policy": "identical candles deduplicated; conflicting candles rejected",
            "include_minute": include_minute,
            "coverage": coverage,
        }
        (staging / "conversion_manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        if destination.exists():
            raise FileExistsError(f"Output appeared during conversion: {destination}")
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Split one-minute Kite data into per-symbol five-minute and daily CSVs.",
    )
    parser.add_argument(
        "--input",
        nargs="+",
        type=Path,
        help="Saved download folders or minute CSVs; default: latest saved Kite download",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New output directory; default: data/private/kite_converted/<input name>",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="Only convert these symbols; default: all symbols including indices",
    )
    parser.add_argument(
        "--include-minute",
        action="store_true",
        help="Also split the original regular-session minutes into 1minute/<symbol>.csv",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=100_000,
        help="CSV rows read per chunk (default: 100000)",
    )
    args = parser.parse_args(argv)
    if args.chunksize < 1:
        parser.error("--chunksize must be positive")
    inputs = args.input
    if inputs is None:
        saved = saved_kite_datasets(STORAGE_ROOT)
        if not saved:
            parser.error("No saved Kite downloads found; supply --input PATH")
        inputs = [saved[0]["path"]]
    name = inputs[0].name if inputs[0].is_dir() else inputs[0].stem
    output = args.output or OUTPUT_ROOT / name
    last_progress = -1

    def report(fraction, message):
        nonlocal last_progress
        step = int(fraction * 20)
        if step > last_progress:
            print(f"{fraction:.0%} {message}", flush=True)
            last_progress = step

    try:
        destination, manifest = convert_kite_minutes(
            inputs,
            output,
            symbols=args.symbols,
            include_minute=args.include_minute,
            chunksize=args.chunksize,
            progress=report,
        )
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(1, f"Conversion failed: {exc}\n")
    coverage = manifest["coverage"]
    print(f"Converted {len(coverage)} symbols to {destination}")
    print(f"Five-minute candles: {sum(row['five_minute_rows'] for row in coverage):,}")
    print(f"Daily candles: {sum(row['daily_rows'] for row in coverage):,}")
    print("Coverage and exclusions: conversion_manifest.json")


if __name__ == "__main__":
    main()
