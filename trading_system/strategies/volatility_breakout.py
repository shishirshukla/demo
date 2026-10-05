from .base import BaseStrategy, compact


class VolatilityBreakout(BaseStrategy):
    name = "volatility_breakout"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if regime.label == "BEAR" or (
            regime.label == "FLAT" and not regime.directional
        ):
            return []
        p, signals = self.parameters, []
        for _, r in market_data.iterrows():
            if (
                r.vol_rank <= p["volatility_percentile"]
                and r.atr_rank <= p["atr_percentile"]
                and r.close > r.sma50 > r.sma200
                and r.close > r.breakout_high
                and r.volume >= p["volume_multiplier"] * r.avg_volume
            ):
                entry = r.breakout_high + p["entry_buffer_atr"] * r.atr
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "LONG",
                        entry - p["stop_atr"] * r.atr,
                        r.volume / r.avg_volume - r.vol_rank + r.rs20_rank,
                        order_type="STOP",
                        entry=entry,
                    )
                )
        return compact(signals)

    def exit_reason(self, position, row):
        return "ten_day_low" if row.close < row.prior_low10 else None
