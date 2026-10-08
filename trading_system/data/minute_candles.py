"""Validation and bounded-memory reading of one-minute bar-start CSVs."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
TZ = "Asia/Kolkata"


def validate_kite_manifest(folder):
    manifest = json.loads(
        (folder / "download_manifest.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("provider") != "Zerodha Kite Connect"
        or manifest.get("interval") != "minute"
        or manifest.get("timestamp_convention") != "bar start (Kite native)"
    ):
        raise ValueError(f"{folder.name}: expected Kite one-minute bar-start data")


def deduplicate_minute_candles(frame):
    frame = frame.drop_duplicates()
    if frame.duplicated(["timestamp", "symbol"]).any():
        raise ValueError(
            "Selected downloads contain conflicting candles for the same symbol and minute. Select non-conflicting datasets."
        )
    return frame


def normalize_minute_candles(frame):
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError("Saved candles are missing minute OHLCV columns")
    frame = frame[COLUMNS].copy()
    timestamps = pd.to_datetime(frame.timestamp, errors="raise")
    frame["timestamp"] = (
        timestamps.dt.tz_localize(TZ)
        if timestamps.dt.tz is None
        else timestamps.dt.tz_convert(TZ)
    )
    for column in COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame.timestamp.isna().any() or frame.symbol.isna().any():
        raise ValueError("Saved candles contain missing symbols or timestamps")
    if frame.symbol.astype(str).str.strip().eq("").any():
        raise ValueError("Saved candles contain empty symbols")
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
    return frame


def iter_minute_candles(paths, *, symbols=None, progress=None, chunksize=100_000):
    """Yield validated, deduplicated candles for one symbol at a time.

    Private temporary partitions support arbitrary input ordering and overlaps
    across files/chunks. Close the iterator if processing stops before exhaustion.
    Memory scales with one instrument's history rather than the entire universe.
    """
    if chunksize < 1:
        raise ValueError("Chunk size must be positive")
    files = list(dict.fromkeys(Path(path).resolve() for path in paths))
    if not files:
        raise ValueError("Select at least one minute CSV")
    selected = list(dict.fromkeys(symbols)) if symbols is not None else None
    if selected == []:
        raise ValueError("Select at least one symbol")
    sizes = [path.stat().st_size for path in files]
    total_bytes, read_bytes = sum(sizes), 0
    with tempfile.TemporaryDirectory(prefix="kite_minutes_") as temporary:
        partitions, part_number = {}, 0
        for path, size in zip(files, sizes, strict=True):
            with path.open("rb") as source:
                for chunk in pd.read_csv(
                    source,
                    usecols=COLUMNS,
                    dtype={"symbol": "str"},
                    chunksize=chunksize,
                ):
                    if selected is not None:
                        chunk = chunk.loc[chunk.symbol.isin(selected)]
                    if not chunk.empty:
                        chunk = normalize_minute_candles(chunk)
                        for symbol, part in chunk.groupby("symbol", sort=False):
                            target = Path(temporary) / f"{part_number}.pkl"
                            # Only read pickles created here, never user-supplied pickles.
                            part.to_pickle(target)
                            partitions.setdefault(symbol, []).append(target)
                            part_number += 1
                    if progress:
                        progress(
                            min(0.5, 0.5 * (read_bytes + source.tell()) / total_bytes),
                            f"Reading saved candles: {path.parent.name}/{path.name}",
                        )
            read_bytes += size
        if selected is not None:
            if missing := set(selected) - set(partitions):
                raise ValueError(f"Missing downloaded candles: {sorted(missing)}")
        if not partitions:
            raise ValueError("No minute candles found in the input CSVs")
        order = selected if selected is not None else sorted(partitions)
        for number, symbol in enumerate(order, start=1):
            if progress:
                progress(
                    0.5 + 0.5 * (number - 1) / len(order),
                    f"Preparing {symbol} ({number}/{len(order)})",
                )
            candles = deduplicate_minute_candles(
                pd.concat(
                    [pd.read_pickle(path) for path in partitions[symbol]],
                    ignore_index=True,
                )
            )
            yield symbol, candles.sort_values("timestamp").reset_index(drop=True)
            del candles
            for path in partitions[symbol]:
                path.unlink()
    if progress:
        progress(1.0, "Finished processing minute data")
