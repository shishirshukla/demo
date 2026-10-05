from .base import BaseStrategy, compact


class WeaknessBreakdown(BaseStrategy):
    name = "weakness_breakdown"
    timeframe = "intraday"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if regime.label != "BEAR":
            return []
        p, signals = self.parameters, []
        for _, r in market_data.iterrows():
            if (
                r.rs20_rank <= p["rs_percentile"]
                and r.daily_close < r.sma20 < r.sma50
                and r.close < r.support
                and r.close < r.vwap
                and r.relative_volume >= p["relative_volume"]
            ):
                stop = min(r.vwap, r.close + p["stop_atr"] * r.atr_intraday)
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "SHORT",
                        stop,
                        1 - r.rs20_rank + r.relative_volume,
                        r.close - p["max_r_target"] * (stop - r.close),
                    )
                )
        return compact(signals)

    def exit_reason(self, position, row):
        return "vwap_invalidation" if row.close > row.vwap else None
