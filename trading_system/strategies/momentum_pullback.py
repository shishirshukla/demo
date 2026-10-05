from .base import BaseStrategy, compact


class MomentumPullback(BaseStrategy):
    name = "momentum_pullback"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if regime.label != "BULL":
            return []
        p, signals = self.parameters, []
        for _, r in market_data.iterrows():
            depth = 1 - r.close / r.prior_high10
            if (
                r.rs20_rank >= p["rs20_percentile"]
                and r.rs60_rank >= p["rs60_percentile"]
                and r.close > r.sma50 > r.sma200
                and p["pullback_min"] <= depth <= p["pullback_max"]
                and (r.close > r.ema20 or r.close > r.sma50)
                and r.close > r.previous_high
            ):
                stop = max(r.swing_low, r.close - p["atr_stop"] * r.atr)
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "LONG",
                        stop,
                        r.rs20_rank + r.rs60_rank - depth,
                    )
                )
        return compact(signals)

    def exit_reason(self, position, row):
        position.below_ema = position.below_ema + 1 if row.close < row.ema20 else 0
        if position.below_ema >= 2:
            return "two_closes_below_ema20"
        return None
