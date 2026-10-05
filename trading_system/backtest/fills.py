def entry_fill(signal, bar):
    if signal.order_type == "MARKET":
        return float(bar.open)
    if signal.order_type == "STOP":
        if signal.side == "LONG" and bar.high >= signal.entry_price:
            return float(max(bar.open, signal.entry_price))
        if signal.side == "SHORT" and bar.low <= signal.entry_price:
            return float(min(bar.open, signal.entry_price))
    if signal.order_type == "LIMIT":
        if signal.side == "LONG" and bar.low <= signal.entry_price:
            return float(min(bar.open, signal.entry_price))
        if signal.side == "SHORT" and bar.high >= signal.entry_price:
            return float(max(bar.open, signal.entry_price))
    return None


def protective_fill(position, bar):
    """If stop and target both touched, assume stop first (conservative)."""
    stop, target = position.stop_price, position.signal.target_price
    if position.direction == 1:
        if bar.low <= stop:
            return min(bar.open, stop), "stop"
        if target is not None and bar.high >= target:
            return max(bar.open, target), "target"
    else:
        if bar.high >= stop:
            return max(bar.open, stop), "stop"
        if target is not None and bar.low <= target:
            return min(bar.open, target), "target"
    return None, None
