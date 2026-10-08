import hashlib
import json
import platform
from pathlib import Path

import pandas as pd

from trading_system.backtest.metrics import daily_curve, metrics, period_returns


def breakdowns(result):
    t = result.trades.copy()
    t["year"] = pd.to_datetime(t.entry_time).dt.year
    tables = {}
    for dimension in [
        "strategy",
        "year",
        "regime",
        "symbol",
        "sector",
        "volatility_quartile",
        "liquidity_quartile",
        "side",
    ]:
        keys = ["strategy", dimension] if dimension != "strategy" else ["strategy"]
        tables[dimension] = (
            t.groupby(keys, dropna=False)
            .agg(
                trades=("net_pnl", "count"),
                net_pnl=("net_pnl", "sum"),
                costs=("costs", "sum"),
                win_rate=("net_pnl", lambda s: (s > 0).mean()),
                average_r=("r_multiple", "mean"),
            )
            .reset_index()
        )
    return tables


def portfolio_series(result):
    e = daily_curve(result).copy()
    e["drawdown"] = e.equity / e.equity.cummax().clip(lower=result.initial_capital) - 1
    ret = e.equity.pct_change()
    e["rolling_sharpe_60"] = ret.rolling(60).mean() / ret.rolling(60).std() * 252**0.5
    e["rolling_drawdown_60"] = (
        e.equity
        / e.equity.rolling(60, min_periods=1).max().clip(lower=result.initial_capital)
        - 1
    )
    e["capital_utilization"] = e.exposure / e.equity
    return e


def overlap(result):
    exposure = pd.DataFrame(
        result.equity.strategy_exposure.tolist(), index=result.equity.timestamp
    ).fillna(0)
    active = (exposure > 0).astype(int)
    return active.T.dot(active) / max(1, len(active))


def signal_export(result, symbol=None):
    """Keep nested signal features machine-readable in CSV exports."""
    table = result.signal_log
    if symbol is not None:
        table = table.loc[table.symbol.eq(symbol)]
    table = table.copy()
    if "features" in table:
        table["features"] = table.features.map(
            lambda values: pd.Series(values).to_json(
                date_format="iso", double_precision=15
            )
        )
    return table


def export_results(results, directory, dataset=None, progress=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    summaries = []
    results[0].daily_screener.to_csv(directory / "daily_screener.csv", index=False)
    for i, r in enumerate(results):
        if progress:
            progress(
                0.8 * i / len(results),
                f"Writing {r.slippage_bps:g} bps reports: trades, signals, trigger diagnostics, portfolio",
            )
        folder = directory / f"slippage_{r.slippage_bps:g}bps"
        folder.mkdir(exist_ok=True)
        summary = {"slippage_bps": r.slippage_bps, **metrics(r)}
        summaries.append(summary)
        for name, table in {
            "equity": r.equity,
            "trades": r.trades,
            "regimes": r.regimes,
            "signals": signal_export(r),
            "trigger_diagnostics": r.diagnostics,
            **{f"by_{k}": v for k, v in breakdowns(r).items()},
        }.items():
            table.to_csv(folder / f"{name}.csv", index=False)
        portfolio_series(r).to_csv(folder / "portfolio_daily.csv")
        period_returns(r, "ME").to_csv(folder / "monthly_returns.csv")
        period_returns(r, "YE").to_csv(folder / "yearly_returns.csv")
        overlap(r).to_csv(folder / "strategy_overlap.csv")
        contribution = r.trades.groupby("symbol").net_pnl.sum().sort_values()
        contribution.head(10).to_csv(folder / "bottom_contributors.csv")
        contribution.tail(10).sort_values(ascending=False).to_csv(
            folder / "top_contributors.csv"
        )
        pd.DataFrame(
            r.equity.sector_exposure.tolist(), index=r.equity.timestamp
        ).fillna(0).to_csv(folder / "sector_exposure.csv")
        monthly = (
            r.trades.assign(
                month=pd.to_datetime(r.trades.exit_time).dt.strftime("%Y-%m")
            )
            .groupby("month")
            .net_pnl.sum()
        )
        monthly.to_csv(folder / "monthly_realized_pnl.csv")
        payload = {
            "metrics": summary,
            "rejections": r.rejections,
            "synthetic": r.synthetic,
            "settings": r.settings,
            "trades": json.loads(r.trades.to_json(orient="records", date_format="iso")),
        }
        (folder / "report.json").write_text(
            json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
        )
    pd.DataFrame(summaries).to_csv(directory / "slippage_comparison.csv", index=False)
    if progress:
        progress(0.85, "Computing dataset fingerprints for reproducible reports")
    fingerprints = {}
    if dataset:
        for name in [
            "daily",
            "benchmark",
            "universe",
            "intraday",
            "benchmark_intraday",
        ]:
            frame = getattr(dataset, name)
            if frame is not None:
                fingerprints[name] = hashlib.sha256(
                    frame.to_csv(index=False).encode()
                ).hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "data_sha256": fingerprints,
                "python": platform.python_version(),
                "pandas": pd.__version__,
                "synthetic": results[0].synthetic,
                "price_basis": "consistently adjusted OHLC",
                "cost_rates": "constant configurable research assumptions",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if progress:
        progress(1.0, "Reports and daily screener saved")
    return directory
