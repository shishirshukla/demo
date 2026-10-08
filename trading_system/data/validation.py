import numpy as np
import pandas as pd

TZ = "Asia/Kolkata"


def validate_bars(frame, intraday=False):
    frame = frame.copy()
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    if missing := required - set(frame.columns):
        raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")
    ts = pd.to_datetime(frame.timestamp, errors="raise")
    frame["timestamp"] = (
        ts.dt.tz_localize(TZ) if ts.dt.tz is None else ts.dt.tz_convert(TZ)
    )
    if frame.timestamp.isna().any():
        raise ValueError("OHLCV contains missing timestamps")
    numeric = ["open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame[numeric].to_numpy()).all():
        raise ValueError("OHLCV contains NaN or infinite values")
    if (frame[numeric[:4]] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError("Prices must be positive and volume non-negative")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("Invalid OHLC ranges")
    if frame.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("Duplicate symbol/timestamp bars")
    if intraday:
        minute = frame.timestamp.dt.hour * 60 + frame.timestamp.dt.minute
        # Timestamps describe bar CLOSE: first complete five-minute bar is 09:20.
        if (
            (minute < 560)
            | (minute > 930)
            | (minute % 5 != 0)
            | frame.timestamp.ne(frame.timestamp.dt.floor("5min"))
        ).any():
            raise ValueError("5-minute close timestamps must be 09:20–15:30 IST")
    else:
        frame["timestamp"] = frame.timestamp.dt.normalize() + pd.Timedelta(
            hours=15, minutes=30
        )
        if frame.duplicated(["symbol", "timestamp"]).any():
            raise ValueError("More than one daily bar per symbol/session")
    return frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def validate_universe(frame):
    frame = frame.copy()
    if not {"symbol", "sector"} <= set(frame):
        raise ValueError("Universe requires symbol and sector")
    for column in ["active_from", "active_to"]:
        frame[column] = pd.to_datetime(
            frame.get(column, pd.Series(pd.NaT, index=frame.index)), errors="raise"
        )
    if "lot_size" not in frame:
        frame["lot_size"] = 1
    frame["lot_size"] = pd.to_numeric(frame.lot_size)
    if ((frame.lot_size < 1) | (frame.lot_size % 1 != 0)).any():
        raise ValueError("lot_size must be a positive integer")
    if (frame.active_to < frame.active_from).any():
        raise ValueError("Membership end precedes start")
    for _, group in frame.groupby("symbol"):
        spans = group.sort_values("active_from", na_position="first")
        end = None
        for row in spans.itertuples():
            start = row.active_from if pd.notna(row.active_from) else pd.Timestamp.min
            if end is not None and start <= end:
                raise ValueError("Overlapping universe membership intervals")
            end = row.active_to if pd.notna(row.active_to) else pd.Timestamp.max
    return frame
