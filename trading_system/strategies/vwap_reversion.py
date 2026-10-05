from .base import BaseStrategy, compact


class VWAPReversion(BaseStrategy):
    name = "vwap_reversion"
    timeframe = "intraday"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        p, signals = self.parameters, []
        if (
            regime.label != "FLAT"
            or regime.adx >= p["adx_max"]
            or not p["breadth_min"] <= regime.breadth <= p["breadth_max"]
        ):
            return []
        for _, r in market_data.iterrows():
            if r["std"] <= 0:
                continue
            deviation = (r.close - r.vwap) / r["std"]
            if (
                deviation < -p["deviation_std"]
                and r.rsi5 < p["rsi_long"]
                and r.residual < -p["residual_min"]
                and r.close > r.previous_high_intraday
            ):
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "LONG",
                        r.recent_low - p["stop_buffer_atr"] * r.atr_intraday,
                        abs(deviation),
                        r.vwap,
                    )
                )
            elif (
                deviation > p["deviation_std"]
                and r.rsi5 > p["rsi_short"]
                and r.residual > p["residual_min"]
                and r.close < r.previous_low_intraday
            ):
                signals.append(
                    self.make_signal(
                        r,
                        timestamp,
                        regime,
                        "SHORT",
                        r.recent_high + p["stop_buffer_atr"] * r.atr_intraday,
                        abs(deviation),
                        r.vwap,
                    )
                )
        return compact(signals)
