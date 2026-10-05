class CostModel:
    def __init__(self, settings, slippage_bps=5):
        self.settings = settings
        self.slippage_bps = slippage_bps

    def execution_price(self, price, buy):
        friction = (self.slippage_bps + self.settings["spread_bps"] / 2) / 10000
        return price * (1 + friction if buy else 1 - friction)

    def fees(self, price, quantity, buy, intraday):
        c, value = self.settings, price * quantity
        brokerage = (
            min(c["brokerage_cap"], value * c["brokerage_rate"])
            if intraday
            else value * c["delivery_brokerage_rate"]
        )
        exchange, sebi = value * c["exchange_rate"], value * c["sebi_rate"]
        gst = (brokerage + exchange + sebi) * c["gst_rate"]
        stt = (
            value * (c["intraday_sell_stt"] if not buy else 0)
            if intraday
            else value * c["delivery_stt"]
        )
        stamp = (
            value * (c["intraday_stamp"] if intraday else c["delivery_stamp"])
            if buy
            else 0
        )
        return brokerage + exchange + sebi + gst + stt + stamp
