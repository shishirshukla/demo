from .base import BaseStrategy, compact


class MomentumPullback(BaseStrategy):
    name = "momentum_pullback"

    def daily_checks(self, f, regime):
        p = self.parameters
        depth = 1 - f.close / f.prior_high10
        return {
            "regime_not_bull": regime.label == "BULL",
            "daily_warmup": f.sma200.notna() & f.rs60_rank.notna(),
            "relative_strength": (f.rs20_rank >= p["rs20_percentile"])
            & (f.rs60_rank >= p["rs60_percentile"]),
            "trend": (f.close > f.sma50) & (f.sma50 > f.sma200),
            "pullback_depth": depth.between(p["pullback_min"], p["pullback_max"]),
            "moving_average": (f.close > f.ema20) | (f.close > f.sma50),
            "reversal_confirmation": f.close > f.previous_high,
        }

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        p, signals = self.parameters, []
        selected = self.select(market_data, self.daily_checks(market_data, regime))
        for _, r in selected.iterrows():
            depth = 1 - r.close / r.prior_high10
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
