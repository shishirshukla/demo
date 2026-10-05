from dataclasses import dataclass

import pandas as pd

from .validation import validate_bars, validate_universe


@dataclass
class Dataset:
    daily: pd.DataFrame
    benchmark: pd.DataFrame
    universe: pd.DataFrame
    intraday: pd.DataFrame | None = None
    benchmark_intraday: pd.DataFrame | None = None
    synthetic: bool = False

    def validate(self):
        self.daily = validate_bars(self.daily)
        self.benchmark = validate_bars(self.benchmark)
        self.universe = validate_universe(self.universe)
        if self.benchmark.symbol.nunique() != 1:
            raise ValueError("Benchmark must contain exactly one index")
        unknown = set(self.daily.symbol) - set(self.universe.symbol)
        if unknown:
            raise ValueError(f"Symbols absent from universe: {sorted(unknown)}")
        if self.intraday is not None:
            self.intraday = validate_bars(self.intraday, True)
            if set(self.intraday.symbol) - set(self.universe.symbol):
                raise ValueError("Intraday symbols absent from universe")
        if self.benchmark_intraday is not None:
            self.benchmark_intraday = validate_bars(self.benchmark_intraday, True)
            if self.benchmark_intraday.symbol.nunique() != 1:
                raise ValueError("Intraday benchmark must contain exactly one index")
        return self


def load_dataset(daily, benchmark, universe, intraday=None, benchmark_intraday=None):
    def read(path):
        return pd.read_csv(path) if path is not None else None

    return Dataset(
        read(daily),
        read(benchmark),
        read(universe),
        read(intraday),
        read(benchmark_intraday),
    ).validate()
