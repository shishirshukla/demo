import numpy as np
import pandas as pd


def streak(values, winning):
    best = current = 0
    for value in values:
        current = current + 1 if (value > 0 if winning else value < 0) else 0
        best = max(best, current)
    return best


def daily_curve(result):
    e = result.equity.set_index("timestamp").sort_index()
    # Last timestamp may have both intraday and daily events.
    return e.resample("D").last().dropna(subset=["equity"])


def metrics(result):
    curve, trades = daily_curve(result), result.trades
    equity = curve.equity
    returns = equity.pct_change()
    returns.iloc[0] = equity.iloc[0] / result.initial_capital - 1
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 252)
    total = equity.iloc[-1] / result.initial_capital - 1
    peak = equity.cummax().clip(lower=result.initial_capital)
    dd = equity / peak - 1
    std, downside = returns.std(), np.sqrt(np.mean(np.minimum(returns, 0) ** 2))
    sharpe = float(returns.mean() / std * np.sqrt(252)) if std > 0 else 0
    sortino = float(returns.mean() / downside * np.sqrt(252)) if downside > 0 else None
    cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1
    wins, losses = trades[trades.net_pnl > 0], trades[trades.net_pnl < 0]
    avg_win = float(wins.net_pnl.mean()) if len(wins) else 0
    avg_loss = float(losses.net_pnl.mean()) if len(losses) else 0
    n = len(trades)
    holding = (
        (
            pd.to_datetime(trades.exit_time) - pd.to_datetime(trades.entry_time)
        ).dt.total_seconds()
        / 86400
        if n
        else pd.Series(dtype=float)
    )
    return {
        "total_return": float(total),
        "cagr": cagr,
        "annual_return": float(returns.mean() * 252),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": float(dd.min()),
        "calmar": cagr / abs(dd.min()) if dd.min() < 0 else None,
        "profit_factor": float(wins.net_pnl.sum() / abs(losses.net_pnl.sum()))
        if len(losses)
        else None,
        "expectancy": (len(wins) * avg_win + len(losses) * avg_loss) / n if n else 0,
        "win_rate": len(wins) / n if n else 0,
        "average_winner": avg_win,
        "average_loser": avg_loss,
        "average_r": float(trades.r_multiple.mean()) if n else 0,
        "median_r": float(trades.r_multiple.median()) if n else 0,
        "trades": n,
        "trades_per_year": n / years,
        "turnover": float(curve.turnover.sum() / result.initial_capital),
        "exposure_percentage": float((result.equity.exposure > 0).mean()),
        "average_holding_days": float(holding.mean()) if n else 0,
        "maximum_consecutive_losses": streak(trades.net_pnl, False),
        "maximum_consecutive_wins": streak(trades.net_pnl, True),
        "final_equity": float(equity.iloc[-1]),
        "total_costs": float(trades.costs.sum()),
    }


def period_returns(result, frequency):
    equity = daily_curve(result).equity.resample(frequency).last().dropna()
    out = equity.pct_change()
    out.iloc[0] = equity.iloc[0] / result.initial_capital - 1
    return out.rename("return").to_frame()
