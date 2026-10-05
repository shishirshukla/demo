"""Historical one-minute Kite candles with atomic local storage."""

import io
import json
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Asia/Kolkata"
STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "private" / "kite"


def kite_client(api_key):
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:
        raise RuntimeError(
            'Install Kite support: uv pip install --python .venv/Scripts/python.exe -e ".[kite-data]"'
        ) from exc
    if not api_key.strip():
        raise ValueError("Enter your Kite API key")
    return KiteConnect(api_key=api_key.strip(), timeout=30)


def api_error(exc):
    """Provider exceptions may contain request details; show safe messages."""
    kind = type(exc).__name__
    if kind == "TokenException":
        return "Kite session expired or invalid. Connect again with a fresh token."
    if kind == "PermissionException":
        return "Kite denied access. Check your app's historical market-data access."
    if kind == "InputException":
        return "Kite rejected the request. Check the instrument and date range."
    if kind in {"Timeout", "ReadTimeout", "ConnectionError", "NetworkException"}:
        return "Could not reach Kite. Check your connection and try again."
    return "Kite request failed. Check credentials, market-data access, and connection."


def nse_instruments(client):
    try:
        frame = pd.DataFrame(client.instruments("NSE"))
    except Exception as exc:
        raise RuntimeError(api_error(exc)) from None
    if not {"instrument_token", "tradingsymbol", "exchange"}.issubset(frame.columns):
        raise ValueError("Invalid NSE instrument list returned by Kite")
    frame = frame.loc[frame.exchange == "NSE"].copy()
    if frame.empty or frame.instrument_token.duplicated().any():
        raise ValueError("Empty or duplicate NSE instrument list returned by Kite")
    return frame.sort_values("tradingsymbol").reset_index(drop=True)


def local_time(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(TZ) if stamp.tzinfo is None else stamp.tz_convert(TZ)


def date_windows(start, end, now=None):
    """Inclusive dates, disjoint 30-day batches, excluding the live minute."""
    current = local_time(now) if now is not None else pd.Timestamp.now(tz=TZ)
    first, last = local_time(start).normalize(), local_time(end).normalize()
    if pd.isna(first) or pd.isna(last) or first > last:
        raise ValueError("Start date must be on or before end date")
    if last > current.normalize():
        raise ValueError("End date cannot be in the future")
    stop = min(last + pd.Timedelta(days=1), current.floor("min"))
    if stop <= first:
        raise ValueError("No completed minutes in the requested range")
    windows = []
    cursor = first
    while cursor < stop:
        edge = min(cursor + pd.Timedelta(days=30), stop)
        windows.append((cursor, edge))
        cursor = edge
    return windows


def normalize_candles(records, instrument, start, stop):
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records).rename(columns={"date": "timestamp"})
    numeric = ["open", "high", "low", "close", "volume"]
    if not {"timestamp", *numeric}.issubset(frame.columns):
        raise ValueError("Kite candles are missing required OHLCV fields")
    frame["timestamp"] = pd.to_datetime(
        frame.timestamp, utc=True, errors="coerce"
    ).dt.tz_convert(TZ)
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.timestamp.isna().any() or not np.isfinite(frame[numeric]).all().all():
        raise ValueError("Invalid timestamps or non-finite OHLCV returned by Kite")
    if (
        (frame[["open", "high", "low", "close"]] <= 0).any().any()
        or (frame.volume < 0).any()
        or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
        or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()
        or (frame.timestamp != frame.timestamp.dt.floor("min")).any()
    ):
        raise ValueError("Inconsistent OHLCV or non-minute timestamps returned by Kite")
    frame = frame.loc[(frame.timestamp >= start) & (frame.timestamp < stop)].copy()
    frame["symbol"] = instrument["tradingsymbol"]
    frame["exchange"] = instrument["exchange"]
    frame["instrument_token"] = int(instrument["instrument_token"])
    return frame[["timestamp", "symbol", "exchange", "instrument_token", *numeric]]


@dataclass
class KiteDownload:
    candles: pd.DataFrame
    instruments: pd.DataFrame
    manifest: dict
    path: Path | None = None

    def to_zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("candles_1minute.csv", self.candles.to_csv(index=False))
            archive.writestr("instruments.csv", self.instruments.to_csv(index=False))
            archive.writestr(
                "download_manifest.json", json.dumps(self.manifest, indent=2)
            )
        return buffer.getvalue()


def download_kite_minutes(
    client, instruments, start, end, *, now=None, progress=None, sleep=time.sleep
):
    if instruments.empty or not {
        "tradingsymbol",
        "exchange",
        "instrument_token",
    }.issubset(instruments.columns):
        raise ValueError("Select at least one NSE instrument")
    if (
        instruments.exchange != "NSE"
    ).any() or instruments.instrument_token.duplicated().any():
        raise ValueError("Select distinct NSE instruments")
    windows = date_windows(start, end, now)
    frames, coverage = [], []
    total, completed = len(windows) * len(instruments), 0
    for instrument in instruments.to_dict("records"):
        chunks, empty_windows = [], 0
        for first, stop in windows:
            for attempt in range(3):
                sleep(0.4 if attempt == 0 else 2**attempt)
                try:
                    records = client.historical_data(
                        int(instrument["instrument_token"]),
                        first.tz_localize(None).to_pydatetime(),
                        (stop - pd.Timedelta(seconds=1))
                        .tz_localize(None)
                        .to_pydatetime(),
                        "minute",
                        continuous=False,
                        oi=False,
                    )
                    break
                except Exception as exc:
                    retryable = type(exc).__name__ in {
                        "NetworkException",
                        "Timeout",
                        "ReadTimeout",
                        "ConnectionError",
                        "DataException",
                    } or getattr(exc, "code", None) in {429, 500, 502, 503, 504}
                    if not retryable or attempt == 2:
                        raise RuntimeError(
                            f"{instrument['tradingsymbol']}: {api_error(exc)} No dataset saved."
                        ) from None
            chunk = normalize_candles(records, instrument, first, stop)
            if chunk.empty:
                empty_windows += 1
            else:
                chunks.append(chunk)
            completed += 1
            if progress:
                progress(
                    completed / total,
                    f"{instrument['tradingsymbol']} - {completed}/{total} batches",
                )
        if not chunks:
            raise ValueError(
                f"No completed candles for {instrument['tradingsymbol']} in this date range. No dataset saved."
            )
        candles = pd.concat(chunks, ignore_index=True).drop_duplicates()
        if candles.timestamp.duplicated().any():
            raise ValueError(
                f"Conflicting duplicate candles for {instrument['tradingsymbol']}. No dataset saved."
            )
        candles = candles.sort_values("timestamp").reset_index(drop=True)
        frames.append(candles)
        coverage.append(
            {
                "symbol": instrument["tradingsymbol"],
                "instrument_token": int(instrument["instrument_token"]),
                "candles": len(candles),
                "first_timestamp": candles.timestamp.iloc[0].isoformat(),
                "last_timestamp": candles.timestamp.iloc[-1].isoformat(),
                "sessions": int(candles.timestamp.dt.date.nunique()),
                "empty_request_windows": empty_windows,
            }
        )
    return KiteDownload(
        pd.concat(frames, ignore_index=True),
        instruments.copy(),
        {
            "provider": "Zerodha Kite Connect",
            "interval": "minute",
            "timezone": TZ,
            "timestamp_convention": "bar start (Kite native)",
            "requested_start": str(local_time(start).date()),
            "requested_end_inclusive": str(local_time(end).date()),
            "effective_end_exclusive": windows[-1][1].isoformat(),
            "downloaded_at": pd.Timestamp.now(tz=TZ).isoformat(),
            "price_basis": "Kite provider OHLCV; no adjustments or resampling applied",
            "coverage": coverage,
            "notes": "Missing minutes and holidays are not filled. Live minute excluded. Availability depends on Kite. Five-minute backtests require aggregation, validation and daily context.",
        },
    )


def save_kite_download(result, label="", *, root=None):
    """Publish a complete new folder after all files have been written."""
    if label and not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", label):
        raise ValueError(
            "Dataset label must use 1-60 letters, digits, underscores or hyphens"
        )
    base = Path(root) if root is not None else STORAGE_ROOT
    base.mkdir(parents=True, exist_ok=True)
    name = (
        pd.Timestamp.now(tz=TZ).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    )
    if label:
        name += "_" + label
    destination = base / name
    staging = Path(tempfile.mkdtemp(prefix=".download_", dir=base))
    try:
        result.candles.to_csv(staging / "candles_1minute.csv", index=False)
        result.instruments.to_csv(staging / "instruments.csv", index=False)
        (staging / "download_manifest.json").write_text(
            json.dumps(result.manifest, indent=2), encoding="utf-8"
        )
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging)
        raise
    result.path = destination.resolve()
    return result.path
