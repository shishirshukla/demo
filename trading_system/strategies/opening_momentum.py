import pandas as pd

from .base import BaseStrategy, compact


class OpeningMomentum(BaseStrategy):
    name = "opening_momentum"
    timeframe = "intraday"

    def daily_checks(self, f, regime):
        p = self.parameters
        bull = regime.label == "BULL"
        return {
            "regime_not_directional": regime.label != "FLAT" and regime.directional,
            "breadth_confirmation": regime.breadth > p["bull_breadth"]
            if bull
            else regime.breadth < p["bear_breadth"],
            "daily_warmup": f.rs20.notna() & f.rs20_rank.notna(),
            "relative_strength": f.rs20 > 0 if bull else f.rs20 < 0,
        }

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if regime.label == "FLAT" or not regime.directional or benchmark_data is None:
            self.last_counts = {"regime_not_directional": len(market_data)}
            if benchmark_data is None:
                self.last_counts = {"missing_intraday_benchmark": len(market_data)}
            return []
        p, signals = self.parameters, []
        f, b = market_data, benchmark_data
        bull = regime.label == "BULL"
        benchmark_ok = b is not None and (
            (b.close > b.previous_close and b.close > b.vwap)
            if bull
            else (b.close < b.previous_close and b.close < b.vwap)
        )
        checks = {
            **self.daily_checks(f, regime),
            "missing_intraday_benchmark": b is not None,
            "benchmark_vwap_unavailable": pd.notna(b.vwap),
            "benchmark_confirmation": benchmark_ok,
            "relative_volume": f.relative_volume >= p["relative_volume"],
            "opening_range_incomplete": f.or_high.notna() & f.or_low.notna(),
            "price_confirmation": (
                (f.close > f.daily_close) & (f.close > f.vwap) & (f.close > f.or_high)
            )
            if bull
            else (
                (f.close < f.daily_close) & (f.close < f.vwap) & (f.close < f.or_low)
            ),
        }
        for _, r in self.select(f, checks).iterrows():
            midpoint = (r.or_high + r.or_low) / 2
            side = "LONG" if bull else "SHORT"
            score = r.relative_volume + (r.rs20_rank if bull else 1 - r.rs20_rank)
            target = r.close + p["target_r"] * (r.close - midpoint)
            signals.append(
                self.make_signal(r, timestamp, regime, side, midpoint, score, target)
            )
        return compact(signals)

    def exit_reason(self, position, row):
        invalid = (
            row.close < row.vwap if position.direction == 1 else row.close > row.vwap
        )
        return "vwap_invalidation" if invalid else None
