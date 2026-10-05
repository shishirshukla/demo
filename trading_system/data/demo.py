"""Seeded fictional data for exercising the application, never market evidence."""

import numpy as np
import pandas as pd

from .historical import Dataset


def demo_dataset(seed=42, sessions=650, intraday_sessions=30):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=sessions, tz="Asia/Kolkata")
    drift = np.select(
        [np.arange(sessions) < sessions * 0.45, np.arange(sessions) < sessions * 0.70],
        [0.0014, -0.0020],
        default=0.0010,
    )
    market = drift + rng.normal(0, 0.004, sessions)
    benchmark_close = 17000 * np.exp(np.cumsum(market))
    daily_records, bench_records = [], []
    symbols = [f"DEMO{i:02d}" for i in range(1, 9)]
    paths = {}
    for i, symbol in enumerate(symbols):
        ret = market * (0.7 + i * 0.08) + rng.normal(0.0002, 0.007, sessions)
        # Periodic quiet windows followed by participation shocks.
        quiet = (np.arange(sessions) % 45) < 20
        ret[quiet] *= 0.3
        ret[np.arange(sessions) % 45 == 20] += 0.025
        close = (250 + 65 * i) * np.exp(np.cumsum(ret))
        previous = np.r_[close[0], close[:-1]]
        opens = previous * np.exp(rng.normal(0, 0.002, sessions))
        paths[symbol] = (opens, close)
        for j, day in enumerate(dates):
            vol = int(rng.uniform(1.2e6, 1.7e6) * (2.5 if j % 45 == 20 else 1))
            daily_records.append(
                {
                    "timestamp": day + pd.Timedelta(hours=15, minutes=30),
                    "symbol": symbol,
                    "open": opens[j],
                    "high": max(opens[j], close[j]) * 1.004,
                    "low": min(opens[j], close[j]) * 0.996,
                    "close": close[j],
                    "volume": vol,
                    "adjusted_close": close[j],
                }
            )
    prev = np.r_[benchmark_close[0], benchmark_close[:-1]]
    for j, day in enumerate(dates):
        bench_records.append(
            {
                "timestamp": day + pd.Timedelta(hours=15, minutes=30),
                "symbol": "NIFTY50",
                "open": prev[j],
                "high": max(prev[j], benchmark_close[j]) * 1.002,
                "low": min(prev[j], benchmark_close[j]) * 0.998,
                "close": benchmark_close[j],
                "volume": 1e8,
            }
        )
    universe = pd.DataFrame(
        {
            "symbol": symbols,
            "sector": ["Technology", "Finance", "Energy", "Consumer"] * 2,
            "index_group": "SYNTHETIC",
            "lot_size": 1,
            "is_fno": False,
            "active_from": "2020-01-01",
            "active_to": None,
        }
    )
    intra_records, intra_bench = [], []
    daily = pd.DataFrame(daily_records)
    bench = pd.DataFrame(bench_records)
    if intraday_sessions:
        for j in range(max(1, sessions - intraday_sessions), sessions):
            day = dates[j]
            for symbol in symbols + ["NIFTY50"]:
                op, cl = (
                    (paths[symbol][0][j], paths[symbol][1][j])
                    if symbol != "NIFTY50"
                    else (prev[j], benchmark_close[j])
                )
                noise = rng.normal(
                    0, 0.0015 if symbol != "NIFTY50" else 0.0004, 75
                ).cumsum()
                noise -= np.linspace(0, noise[-1], 75)
                closes = op * np.exp(np.linspace(0, np.log(cl / op), 75) + noise)
                opens = np.r_[op, closes[:-1]]
                records = intra_records if symbol != "NIFTY50" else intra_bench
                volume = (
                    daily.loc[
                        (daily.symbol == symbol)
                        & (daily.timestamp.dt.date == day.date()),
                        "volume",
                    ].iloc[0]
                    if symbol != "NIFTY50"
                    else 1e8
                )
                session_rows = []
                for k in range(75):
                    row = {
                        "timestamp": day + pd.Timedelta(minutes=560 + k * 5),
                        "symbol": symbol,
                        "open": opens[k],
                        "high": max(opens[k], closes[k]) * 1.0008,
                        "low": min(opens[k], closes[k]) * 0.9992,
                        "close": closes[k],
                        "volume": volume / 75,
                    }
                    records.append(row)
                    session_rows.append(row)
                table = daily if symbol != "NIFTY50" else bench
                mask = (table.symbol == symbol) & (
                    table.timestamp.dt.date == day.date()
                )
                table.loc[mask, "high"] = max(r["high"] for r in session_rows)
                table.loc[mask, "low"] = min(r["low"] for r in session_rows)
    return Dataset(
        daily,
        bench,
        universe,
        pd.DataFrame(intra_records) if intra_records else None,
        pd.DataFrame(intra_bench) if intra_bench else None,
        True,
    ).validate()
