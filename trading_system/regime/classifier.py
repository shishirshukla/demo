import pandas as pd

from trading_system.indicators import adx, breadth, ema
from trading_system.models import Regime


class RegimeClassifier:
    def __init__(self, settings):
        self.settings = settings

    def prepare(self, benchmark):
        b = benchmark.copy().sort_values("timestamp")
        c = self.settings
        b["ema_fast"] = ema(b.close, c["ema_fast"])
        b["ema_slow"] = ema(b.close, c["ema_slow"])
        b["slope"] = b.ema_fast.diff(c["slope_sessions"])
        b["adx"] = adx(b)
        b["return20"] = b.close.pct_change(20)
        b["return60"] = b.close.pct_change(60)
        return b

    def classify(self, benchmark_row, universe_rows):
        b, c = benchmark_row, self.settings
        width = breadth(universe_rows)
        if b is None or pd.isna(b.get("ema_slow")):
            return Regime(breadth=width)
        label = "FLAT"
        if (
            b.close > b.ema_fast > b.ema_slow
            and b.slope > 0
            and width > c["breadth_bull"]
        ):
            label = "BULL"
        elif (
            b.close < b.ema_fast < b.ema_slow
            and b.slope < 0
            and width < c["breadth_bear"]
        ):
            label = "BEAR"
        strength = float(b.adx) if pd.notna(b.adx) else 0
        return Regime(label, strength >= c["adx_directional"], width, strength)
