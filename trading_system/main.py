import argparse
import json
import logging
from pathlib import Path
from time import monotonic

import pandas as pd

from trading_system.backtest import BacktestEngine
from trading_system.backtest.preparation import prepare_backtest
from trading_system.config import load_settings
from trading_system.data import demo_dataset, load_dataset
from trading_system.data.converted import load_converted_dataset
from trading_system.reports import export_results


def main():
    p = argparse.ArgumentParser(description="NIFTY strategy research backtest")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--settings")
    p.add_argument("--daily")
    p.add_argument(
        "--converted",
        help="Generated Kite folder containing daily/, 5minute/ and conversion_manifest.json",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        help="Converted stocks (default: every non-benchmark symbol)",
    )
    p.add_argument("--benchmark-symbol", default="NIFTY 50")
    p.add_argument("--benchmark")
    p.add_argument(
        "--universe",
        help="Universe / sectors CSV (generated candles default to Unknown sectors)",
    )
    p.add_argument("--intraday")
    p.add_argument("--benchmark-intraday")
    p.add_argument("--mode", choices=["daily", "intraday", "hybrid"], default="daily")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--output", default="output/latest")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        settings = load_settings(args.settings)
        last_update = [0.0]

        def progress(fraction, message):
            if monotonic() - last_update[0] >= 2 or fraction == 1:
                logging.info("%3.0f%% %s", fraction * 100, message)
                last_update[0] = monotonic()

        if args.converted:
            manifest = json.loads(
                (Path(args.converted) / "conversion_manifest.json").read_text()
            )
            symbols = args.symbols or [
                r["symbol"]
                for r in manifest["coverage"]
                if r["symbol"] != args.benchmark_symbol
                and r["symbol"] not in ("NIFTY BANK", "INDIA VIX")
            ]
            dataset, _ = load_converted_dataset(
                args.converted,
                symbols,
                args.benchmark_symbol,
                mode=args.mode,
                universe=pd.read_csv(args.universe) if args.universe else None,
                progress=progress,
            )
        elif args.demo:
            dataset = demo_dataset()
        elif args.daily and args.benchmark:
            dataset = load_dataset(
                args.daily,
                args.benchmark,
                args.universe or "config/universe.csv",
                args.intraday,
                args.benchmark_intraday,
            )
        else:
            p.error("Supply --demo, --converted, or both --daily and --benchmark")
        prepared = prepare_backtest(
            dataset, settings, args.start, args.end, args.mode, progress=progress
        )
        results = [
            BacktestEngine(settings).run(
                dataset,
                bps,
                args.start,
                args.end,
                args.mode,
                prepared=prepared,
                progress=progress,
            )
            for bps in settings["costs"]["slippage_scenarios"]
        ]
        export_results(results, Path(args.output), dataset, progress=progress)
        print(f"Reports saved to {Path(args.output).resolve()}")
    except (ValueError, OSError, KeyError):
        logging.exception("Backtest failed; check data schema and settings")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
