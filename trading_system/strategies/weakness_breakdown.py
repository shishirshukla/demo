from .base import BaseStrategy, compact


class WeaknessBreakdown(BaseStrategy):
    name = "weakness_breakdown"
    timeframe = "intraday"

    def daily_checks(self, f, regime):
        close = f.get("daily_close", f.close)
        return {
            "regime_not_bear": regime.label == "BEAR",
            "daily_warmup": f.sma50.notna() & f.rs20_rank.notna() & f.support.notna(),
            "relative_weakness": f.rs20_rank <= self.parameters["rs_percentile"],
            "daily_downtrend": (close < f.sma20) & (f.sma20 < f.sma50),
        }

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if regime.label != "BEAR":
            self.last_counts = {"regime_not_bear": len(market_data)}
            return []
        p, signals = self.parameters, []
        f = market_data
        checks = {
            **self.daily_checks(f, regime),
            "support_breakdown": f.close < f.support,
            "vwap_confirmation": f.close < f.vwap,
            "relative_volume": f.relative_volume >= p["relative_volume"],
        }
        for _, r in self.select(f, checks).iterrows():
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
