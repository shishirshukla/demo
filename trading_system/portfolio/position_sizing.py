import math


def position_size(equity, entry, stop, settings, available, lot_size=1):
    distance = abs(entry - stop)
    if distance <= 0 or entry <= 0 or equity <= 0 or available <= 0:
        return 0
    quantity = min(
        math.floor(equity * settings["risk_per_trade"] / distance),
        math.floor(equity * settings["max_stock_allocation"] / entry),
        math.floor(available / entry),
    )
    return quantity // lot_size * lot_size
