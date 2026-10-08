from .base import BaseStrategy, compact


class VWAPReversion(BaseStrategy):
    name = "vwap_reversion"
    timeframe = "intraday"

    def daily_checks(self, f, regime):
        p = self.parameters
        return {
            "regime_not_flat": regime.label == "FLAT",
            "adx_too_high": regime.adx < p["adx_max"],
            "breadth_outside_range": p["breadth_min"]
            <= regime.breadth
            <= p["breadth_max"],
        }

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        if (
            regime.label != "FLAT"
            or regime.adx >= self.parameters["adx_max"]
            or not self.parameters["breadth_min"]
            <= regime.breadth
            <= self.parameters["breadth_max"]
        ):
            reason = (
                "regime_not_flat"
                if regime.label != "FLAT"
                else "adx_too_high"
                if regime.adx >= self.parameters["adx_max"]
                else "breadth_outside_range"
            )
            self.last_counts = {reason: len(market_data)}
            return []
        p, signals = self.parameters, []
        f = market_data
        deviation = (f.close - f.vwap) / f["std"].where(f["std"] > 0)
        long = (
            (deviation < -p["deviation_std"])
            & (f.rsi5 < p["rsi_long"])
            & (f.residual < -p["residual_min"])
        )
        short = (
            (deviation > p["deviation_std"])
            & (f.rsi5 > p["rsi_short"])
            & (f.residual > p["residual_min"])
        )
        checks = {
            **self.daily_checks(f, regime),
            "intraday_warmup": f["std"].gt(0) & f.atr_intraday.notna(),
            "vwap_rsi_residual": long | short,
            "reversal_confirmation": (long & (f.close > f.previous_high_intraday))
            | (short & (f.close < f.previous_low_intraday)),
        }
        for index, r in self.select(f, checks).iterrows():
            side = "LONG" if long.loc[index] else "SHORT"
            stop = (
                r.recent_low - p["stop_buffer_atr"] * r.atr_intraday
                if side == "LONG"
                else r.recent_high + p["stop_buffer_atr"] * r.atr_intraday
            )
            signals.append(
                self.make_signal(
                    r, timestamp, regime, side, stop, abs(deviation.loc[index]), r.vwap
                )
            )
        return compact(signals)
