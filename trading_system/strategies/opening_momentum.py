from .base import BaseStrategy, compact


class OpeningMomentum(BaseStrategy):
    name = "opening_momentum"
    timeframe = "intraday"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        p, signals = self.parameters, []
        b = benchmark_data
        if b is None or regime.label == "FLAT" or not regime.directional:
            return []
        for _, r in market_data.iterrows():
            if not r.relative_volume >= p["relative_volume"]:
                continue
            midpoint = (r.or_high + r.or_low) / 2
            if (
                regime.label == "BULL"
                and regime.breadth > p["bull_breadth"]
                and b.close > b.previous_close
                and b.close > b.vwap
                and r.close > r.daily_close
                and r.close > r.vwap
                and r.rs20 > 0
                and r.close > r.or_high
            ):
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "LONG",
                        midpoint,
                        r.relative_volume + r.rs20_rank,
                        r.close + p["target_r"] * (r.close - midpoint),
                    )
                )
            elif (
                regime.label == "BEAR"
                and regime.breadth < p["bear_breadth"]
                and b.close < b.previous_close
                and b.close < b.vwap
                and r.close < r.daily_close
                and r.close < r.vwap
                and r.rs20 < 0
                and r.close < r.or_low
            ):
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "SHORT",
                        midpoint,
                        r.relative_volume + 1 - r.rs20_rank,
                        r.close - p["target_r"] * (midpoint - r.close),
                    )
                )
        return compact(signals)

    def exit_reason(self, position, row):
        invalid = (
            row.close < row.vwap if position.direction == 1 else row.close > row.vwap
        )
        return "vwap_invalidation" if invalid else None
