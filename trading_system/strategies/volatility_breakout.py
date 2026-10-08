from .base import BaseStrategy, compact


class VolatilityBreakout(BaseStrategy):
    name = "volatility_breakout"

    def daily_checks(self, f, regime):
        p = self.parameters
        return {
            "regime_not_directional": regime.label != "BEAR"
            and (regime.label != "FLAT" or regime.directional),
            "daily_warmup": f.sma200.notna() & f.vol_rank.notna() & f.atr_rank.notna(),
            "compression": (f.vol_rank <= p["volatility_percentile"])
            & (f.atr_rank <= p["atr_percentile"]),
            "trend": (f.close > f.sma50) & (f.sma50 > f.sma200),
            "breakout_confirmation": f.close > f.breakout_high,
            "breakout_volume": f.volume >= p["volume_multiplier"] * f.avg_volume,
        }

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        p, signals = self.parameters, []
        selected = self.select(market_data, self.daily_checks(market_data, regime))
        for _, r in selected.iterrows():
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
