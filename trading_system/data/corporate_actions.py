def check_adjustments(frame):
    """Reject mixed price bases; loader expects all OHLC consistently adjusted."""
    if "adjusted_close" in frame:
        ratio = frame.adjusted_close / frame.close
        if ((ratio - 1).abs() > 1e-6).any():
            raise ValueError(
                "Supply consistently adjusted OHLC and volume; adjusted_close must equal close. Mixing raw OHLC with adjusted close is unsupported."
            )
    if (
        "corporate_action_flag" in frame
        and frame.corporate_action_flag.fillna(False).astype(bool).any()
    ):
        raise ValueError(
            "Resolve corporate actions to a consistent adjusted price base before backtesting"
        )
