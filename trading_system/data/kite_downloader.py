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
DEFAULT_REQUEST_DELAY = 1.0
MIN_REQUEST_DELAY = 0.4
REQUEST_WINDOW_DAYS = 60


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
        limit = re.search(r"interval exceeds max limit:\s*(\d+)\s*days", str(exc))
        if limit:
            return (
                "Kite rejected a date-window request: one-minute data is limited "
                f"to {limit.group(1)} days per request. Check the requested batch range."
            )
        return "Kite rejected the request. Check the instrument and date range."
    if getattr(exc, "code", None) == 429:
        return (
            "Kite rate limit reached. Increase the request delay and try again later."
        )
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


def request_bounds(start, end, now=None):
    """One inclusive date range, excluding the live minute."""
    current = local_time(now) if now is not None else pd.Timestamp.now(tz=TZ)
    first, last = local_time(start).normalize(), local_time(end).normalize()
    if pd.isna(first) or pd.isna(last) or first > last:
        raise ValueError("Start date must be on or before end date")
    if last > current.normalize():
        raise ValueError("End date cannot be in the future")
    stop = min(last + pd.Timedelta(days=1), current.floor("min"))
    if stop <= first:
        raise ValueError("No completed minutes in the requested range")
    return first, stop


def date_windows(start, end, now=None):
    """Disjoint windows of up to 60 calendar days with exclusive end times."""
    first, stop = request_bounds(start, end, now)
    windows = []
    while first < stop:
        edge = min(first + pd.Timedelta(days=REQUEST_WINDOW_DAYS), stop)
        windows.append((first, edge))
        first = edge
    return windows


def normalize_candles(records, instrument, start, stop, *, skipped=None):
    """Keep valid completed candles, recording skipped rows when requested."""
    if not records:
        return pd.DataFrame()
    context = (
        f"{instrument['tradingsymbol']} (instrument {int(instrument['instrument_token'])}), "
        f"request {start.isoformat()} to "
        f"{(stop - pd.Timedelta(seconds=1)).isoformat()}"
    )
    frame = pd.DataFrame(records).rename(columns={"date": "timestamp"})
    numeric = ["open", "high", "low", "close", "volume"]
    missing = {"timestamp", *numeric} - set(frame.columns)
    if missing:
        raise ValueError(
            f"{context}: Kite candles are missing required fields: "
            f"{', '.join(sorted(missing))}. No dataset saved."
        )
    frame["timestamp"] = pd.to_datetime(
        frame.timestamp, utc=True, errors="coerce"
    ).dt.tz_convert(TZ)
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # Ignore live/out-of-range bars. Invalid timestamps are retained for auditing.
    frame = frame.loc[
        frame.timestamp.isna() | ((frame.timestamp >= start) & (frame.timestamp < stop))
    ].copy()
    checks = {
        "invalid timestamp": frame.timestamp.isna(),
        "non-finite or non-numeric OHLCV": ~np.isfinite(frame[numeric]).all(axis=1),
        "non-positive OHLC price": (frame[["open", "high", "low", "close"]] <= 0).any(
            axis=1
        ),
        "negative volume": frame.volume < 0,
        "high below open/close/low": frame.high
        < frame[["open", "close", "low"]].max(axis=1),
        "low above open/close/high": frame.low
        > frame[["open", "close", "high"]].min(axis=1),
        "non-minute timestamp (seconds or sub-seconds present)": frame.timestamp.notna()
        & (frame.timestamp != frame.timestamp.dt.floor("min")),
    }
    invalid = pd.DataFrame(checks).any(axis=1)
    if skipped is not None:
        for index, row in frame.loc[invalid].iterrows():
            skipped.append(
                {
                    "symbol": instrument["tradingsymbol"],
                    "instrument_token": int(instrument["instrument_token"]),
                    "timestamp": row.timestamp.isoformat()
                    if pd.notna(row.timestamp)
                    else None,
                    **{
                        column: float(row[column]) if np.isfinite(row[column]) else None
                        for column in numeric
                    },
                    "reasons": [
                        reason for reason, mask in checks.items() if mask.loc[index]
                    ],
                }
            )
    frame = frame.loc[~invalid].copy()
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


def validate_request_delay(value):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        delay = float("nan")
    if not np.isfinite(delay) or delay < MIN_REQUEST_DELAY:
        raise ValueError(
            f"Request delay must be finite and at least {MIN_REQUEST_DELAY:g} seconds."
        )
    return delay


def download_kite_minutes(
    client,
    instruments,
    start,
    end,
    *,
    now=None,
    progress=None,
    request_delay=DEFAULT_REQUEST_DELAY,
    sleep=time.sleep,
):
    request_delay = validate_request_delay(request_delay)
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
    selected = instruments.to_dict("records")
    requests = [
        (instrument, first, stop) for instrument in selected for first, stop in windows
    ]
    responses = {int(instrument["instrument_token"]): [] for instrument in selected}
    total = len(requests)
    for completed, (instrument, first, stop) in enumerate(requests, start=1):
        wait = request_delay
        for attempt in range(3):
            sleep(wait)
            try:
                records = client.historical_data(
                    int(instrument["instrument_token"]),
                    first.tz_localize(None).to_pydatetime(),
                    (stop - pd.Timedelta(seconds=1)).tz_localize(None).to_pydatetime(),
                    "minute",
                    continuous=False,
                    oi=False,
                )
                break
            except Exception as exc:
                kind, code = type(exc).__name__, getattr(exc, "code", None)
                retryable = kind in {
                    "NetworkException",
                    "Timeout",
                    "ReadTimeout",
                    "ConnectionError",
                    "DataException",
                } or code in {429, 500, 502, 503, 504}
                if kind in {
                    "TokenException",
                    "PermissionException",
                    "InputException",
                }:
                    retryable = False
                if not retryable or attempt == 2:
                    raise RuntimeError(
                        f"{instrument['tradingsymbol']}: {api_error(exc)} No dataset saved."
                    ) from None
                wait = max(request_delay, (10 if code == 429 else 2) * 2**attempt)
        responses[int(instrument["instrument_token"])].append((first, stop, records))
        if progress:
            batch_number = (completed - 1) % len(windows) + 1
            instrument_number = (completed - 1) // len(windows) + 1
            progress(
                0.9 * completed / total,
                f"{instrument['tradingsymbol']} - date batch {batch_number}/{len(windows)}; "
                f"instrument {instrument_number}/{len(selected)}; "
                f"API request {completed}/{total}",
            )

    frames, coverage, skipped = [], [], []
    for completed, instrument in enumerate(selected, start=1):
        symbol_skipped, chunks = [], []
        batches = responses[int(instrument["instrument_token"])]
        for first, stop, records in batches:
            chunk = normalize_candles(
                records, instrument, first, stop, skipped=symbol_skipped
            )
            if not chunk.empty:
                chunks.append(chunk)
        skipped.extend(symbol_skipped)
        if not chunks:
            raise ValueError(
                f"No completed candles with valid data for {instrument['tradingsymbol']} "
                f"in this date range ({len(symbol_skipped)} skipped). No dataset saved."
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
                "skipped_candles": len(symbol_skipped),
                "empty_request_windows": sum(not records for _, _, records in batches),
            }
        )
        if progress:
            progress(
                0.9 + 0.1 * completed / len(selected),
                f"Processed {instrument['tradingsymbol']} - {len(symbol_skipped)} invalid candles skipped",
            )
    return KiteDownload(
        pd.concat(frames, ignore_index=True),
        instruments.copy(),
        {
            "provider": "Zerodha Kite Connect",
            "interval": "minute",
            "request_delay_seconds": request_delay,
            "download_mode": "60-calendar-day batches, then process",
            "request_window_days": REQUEST_WINDOW_DAYS,
            "date_batches_per_instrument": len(windows),
            "planned_historical_requests": total,
            "skipped_candle_count": len(skipped),
            "skipped_candles": skipped,
            "timezone": TZ,
            "timestamp_convention": "bar start (Kite native)",
            "requested_start": str(local_time(start).date()),
            "requested_end_inclusive": str(local_time(end).date()),
            "effective_end_exclusive": windows[-1][1].isoformat(),
            "downloaded_at": pd.Timestamp.now(tz=TZ).isoformat(),
            "price_basis": "Kite provider OHLCV; no adjustments or resampling applied",
            "coverage": coverage,
            "notes": "Inconsistent candles are skipped and listed in skipped_candles. Missing minutes and holidays are not filled. Live minute excluded. Availability depends on Kite. Five-minute backtests require aggregation, validation and daily context.",
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
