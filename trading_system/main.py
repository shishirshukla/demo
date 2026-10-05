import argparse
import logging
from pathlib import Path

from trading_system.backtest import BacktestEngine
from trading_system.config import load_settings
from trading_system.data import demo_dataset, load_dataset
from trading_system.reports import export_results


def main():
    p = argparse.ArgumentParser(description="NIFTY strategy research backtest")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--settings")
    p.add_argument("--daily")
    p.add_argument("--benchmark")
    p.add_argument("--universe", default="config/universe.csv")
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
        if args.demo:
            dataset = demo_dataset()
        elif args.daily and args.benchmark:
            dataset = load_dataset(
                args.daily,
                args.benchmark,
                args.universe,
                args.intraday,
                args.benchmark_intraday,
            )
        else:
            p.error("Supply --demo or both --daily and --benchmark")
        results = [
            BacktestEngine(settings).run(dataset, bps, args.start, args.end, args.mode)
            for bps in settings["costs"]["slippage_scenarios"]
        ]
        export_results(results, Path(args.output), dataset)
        print(f"Reports saved to {Path(args.output).resolve()}")
    except (ValueError, OSError, KeyError):
        logging.exception("Backtest failed; check data schema and settings")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
