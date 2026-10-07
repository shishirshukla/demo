"""Download historical minute candles for an NSE Market Watch stock list."""

import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd

from .kite_credentials import load_kite_credentials
from .kite_downloader import (
    DEFAULT_REQUEST_DELAY,
    MIN_REQUEST_DELAY,
    STORAGE_ROOT,
    TZ,
    api_error,
    download_kite_minutes,
    kite_client,
    local_time,
    nse_instruments,
    request_bounds,
    save_kite_download,
    validate_request_delay,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def find_stock_file(root=None):
    root = Path(root) if root is not None else PROJECT_ROOT
    files = sorted(root.glob("MW-NIFTY-100*.csv"))
    if not files:
        raise ValueError(
            "No MW-NIFTY-100 CSV found in the project root. Supply --stocks-file."
        )
    if len(files) != 1:
        raise ValueError(
            "Multiple MW-NIFTY-100 CSVs found. Select one with --stocks-file."
        )
    return files[0]


def read_stock_symbols(path):
    """Read only symbols, preserving their order and excluding the summary row."""
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    frame.columns = frame.columns.str.strip().str.upper()
    if list(frame.columns).count("SYMBOL") != 1:
        raise ValueError("The stock list must contain one SYMBOL column.")
    symbols = frame.SYMBOL.str.strip().str.upper()
    if symbols.eq("").any():
        raise ValueError("The stock list contains an empty SYMBOL.")
    symbols = symbols[symbols != "NIFTY 100"].drop_duplicates().tolist()
    if not symbols:
        raise ValueError("The stock list contains no stocks.")
    return symbols


def select_instruments(symbols, master, *, include_benchmark=False):
    """Resolve exact NSE equity symbols; fail before requesting any candles."""
    if not {
        "tradingsymbol",
        "instrument_token",
        "exchange",
        "instrument_type",
        "segment",
    }.issubset(master.columns):
        raise ValueError("Kite instrument list is missing equity/segment metadata.")
    requested = list(symbols)
    equities = (
        (master.exchange == "NSE")
        & (master.segment == "NSE")
        & (master.instrument_type == "EQ")
    )
    eligible = equities
    if include_benchmark:
        requested.append("NIFTY 50")
        eligible = eligible | (
            (master.exchange == "NSE")
            & master.segment.str.contains("INDICES", case=False, na=False)
            & (master.tradingsymbol == "NIFTY 50")
        )
    selected = master.loc[eligible & master.tradingsymbol.isin(requested)].copy()
    available = set(selected.tradingsymbol)
    missing = [symbol for symbol in requested if symbol not in available]
    if missing:
        raise ValueError(
            "Symbols not found in Kite's NSE instrument list: "
            + ", ".join(missing)
            + ". No candles requested."
        )
    if selected.tradingsymbol.duplicated().any():
        raise ValueError("Ambiguous symbols in Kite's NSE instrument list.")
    return (
        selected.set_index("tradingsymbol", drop=False)
        .loc[requested]
        .reset_index(drop=True)
    )


def _date(value):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Dates must use YYYY-MM-DD.") from None


def main(argv=None, *, now=None):
    parser = argparse.ArgumentParser(
        description="Download one-minute Kite OHLCV for stocks in an MW-NIFTY-100 CSV."
    )
    parser.add_argument(
        "--stocks-file",
        type=Path,
        help="Default: the single MW-NIFTY-100 CSV in the project root",
    )
    parser.add_argument(
        "--start", type=_date, required=True, help="Inclusive start date, YYYY-MM-DD"
    )
    parser.add_argument(
        "--end",
        type=_date,
        help="Inclusive end date; default yesterday in Asia/Kolkata",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="Default: saved dashboard Kite credentials",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=STORAGE_ROOT,
        help="Parent directory for a new dataset folder; default data/private/kite",
    )
    parser.add_argument(
        "--label", default="nifty100", help="Optional dataset folder label"
    )
    parser.add_argument(
        "--include-benchmark",
        action="store_true",
        help="Also download NIFTY 50 for use as the backtest benchmark",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY,
        help=f"Minimum seconds between historical requests; default {DEFAULT_REQUEST_DELAY:g}, minimum {MIN_REQUEST_DELAY:g}",
    )
    args = parser.parse_args(argv)
    try:
        delay = validate_request_delay(args.delay)
        current = local_time(now) if now is not None else pd.Timestamp.now(tz=TZ)
        end = args.end or (current - pd.Timedelta(days=1)).date()
        request_bounds(args.start, end, now=current)
        if args.label and not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.label):
            raise ValueError(
                "Dataset label must use 1-60 letters, digits, underscores or hyphens."
            )
        stock_file = args.stocks_file or find_stock_file()
        symbols = read_stock_symbols(stock_file)
        credentials = load_kite_credentials(path=args.credentials_file)
        if credentials is None:
            raise ValueError(
                "No saved Kite credentials. Connect through the dashboard's Download Kite "
                "data menu first, or supply --credentials-file."
            )
        client = kite_client(credentials.api_key)
        client.set_access_token(credentials.access_token)
        try:
            client.profile()
        except Exception as exc:
            raise RuntimeError(
                "Kite account verification failed. " + api_error(exc)
            ) from None
        selected = select_instruments(
            symbols, nse_instruments(client), include_benchmark=args.include_benchmark
        )
        print(f"Loaded {len(symbols)} stocks from {stock_file.name}.", flush=True)
        print(
            f"Downloading one-minute candles from {args.start} through {end} (inclusive, IST).",
            flush=True,
        )
        print(f"Minimum request delay: {delay:g} seconds.", flush=True)
        result = download_kite_minutes(
            client,
            selected,
            args.start,
            end,
            now=current,
            request_delay=delay,
            progress=lambda fraction, message: print(
                f"{fraction:6.1%} {message}", flush=True
            ),
        )
        result.manifest.update(
            {
                "stock_list_file": stock_file.name,
                "requested_stock_symbols": symbols,
                "stock_list_basis": "Fixed user-supplied Market Watch snapshot; not historical index membership",
                "benchmark_symbol": "NIFTY 50" if args.include_benchmark else None,
            }
        )
        destination = save_kite_download(result, args.label, root=args.output)
        print(
            f"Skipped {result.manifest['skipped_candle_count']} invalid candles; "
            "details are recorded in download_manifest.json.",
            flush=True,
        )
        print(
            f"Saved {len(result.candles):,} candles for {len(selected)} instruments to {destination}"
        )
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except OSError:
        parser.exit(
            1,
            "Error: Could not read input files or save the dataset. Check paths, permissions, and disk space.\n",
        )


if __name__ == "__main__":
    main()
