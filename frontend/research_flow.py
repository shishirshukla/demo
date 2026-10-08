"""Explain daily selection, signal triggers, order outcomes and stock trades."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from trading_system.reports.performance import signal_export


def render_diagnostics(result, mode, dataset):
    st.subheader("Backtest flow and diagnostics")
    st.caption(
        "Daily candles → screening / regime → strategy trigger → next 5-minute bar → risk approval → entry / exit. Daily mode uses daily execution bars."
    )
    signals = result.signal_log
    rows = []
    for name, params in result.settings["strategies"].items():
        timeframe = (
            "daily"
            if name in ("momentum_pullback", "volatility_breakout")
            else "intraday"
        )
        active = params["enabled"] and (mode == "hybrid" or mode == timeframe)
        state = "running" if active else "disabled in settings / timeframe"
        if active and name == "opening_momentum":
            if dataset.benchmark_intraday is None:
                state = "blocked: no 5-minute benchmark"
            elif not dataset.benchmark_intraday.volume.gt(0).any():
                state = "blocked: benchmark has no volume for VWAP"
        selected = signals.loc[signals.strategy.eq(name)]
        rows.append(
            {
                "strategy": name,
                "state": state,
                "signals": len(selected),
                "filled": int(selected.status.eq("filled").sum()),
                "rejected": int(selected.status.eq("rejected").sum()),
                "unfilled / expired": int(
                    selected.status.isin(["unfilled", "expired"]).sum()
                ),
                "closed trades": int(result.trades.strategy.eq(name).sum()),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if (
        mode != "daily"
        and dataset.benchmark_intraday is not None
        and not dataset.benchmark_intraday.volume.gt(0).any()
    ):
        st.info(
            "Benchmark candles have zero volume. Opening Momentum requires benchmark VWAP, which cannot be computed from these candles. No volume is invented; this strategy remains blocked when that confirmation is unavailable."
        )
    history = result.daily_screener.groupby("symbol").agg(
        daily_sessions=("timestamp", "size"),
        first_session=("timestamp", "min"),
        last_session=("timestamp", "max"),
    )
    history["has_200_session_history"] = history.daily_sessions >= 200
    if result.trades.empty:
        st.warning(
            "No trades were filled. Review the strategy counts, screening exclusions and trigger blockers below."
        )
    if mode != "hybrid":
        st.info(
            "Choose hybrid to evaluate all five strategies with daily screening and 5-minute execution."
        )
    if not history.has_200_session_history.all():
        st.info(
            f"{int((~history.has_200_session_history).sum())} stocks have fewer than 200 daily candles. Their short history does not remove other stocks’ warmup."
        )
    st.markdown("**Data coverage and warmup**")
    st.dataframe(history.reset_index(), hide_index=True, width="stretch")
    screen = result.daily_screener
    if not screen.empty:
        st.markdown("**Daily screening exclusions**")
        st.dataframe(
            screen.groupby("eligibility_reason")
            .size()
            .rename("stock_sessions")
            .reset_index(),
            hide_index=True,
        )
    if not result.diagnostics.empty:
        st.markdown("**Strategy trigger checks**")
        st.caption(
            "Counts identify the first failed check for each stock evaluation. Intraday strategies evaluate many bars per stock session. “selected” counts valid signals."
        )
        st.dataframe(
            result.diagnostics.groupby(["strategy", "reason"], as_index=False)[
                "count"
            ].sum(),
            hide_index=True,
            width="stretch",
        )
    if not signals.empty:
        st.markdown("**Order outcomes**")
        st.dataframe(
            signals.groupby(["strategy", "status", "reason"])
            .size()
            .rename("signals")
            .reset_index(),
            hide_index=True,
        )
    st.download_button(
        "Download trigger diagnostics",
        result.diagnostics.to_csv(index=False),
        "trigger_diagnostics.csv",
        "text/csv",
    )


def render_daily_screener(result, universe=None):
    st.subheader("Daily stock screener")
    st.caption(
        "These are the actual daily features used for selection. Intraday “selected” means a daily candidate awaiting a 5-minute trigger; swing “selected” means the daily entry conditions passed. Daily context always precedes an intraday signal."
    )
    screen = result.daily_screener
    if screen.empty:
        st.info("No daily screening rows for this run.")
        return
    dates = sorted(screen.timestamp.dt.date.unique())
    day = st.selectbox("Screening session", dates, index=len(dates) - 1)
    current = screen.loc[screen.timestamp.dt.date.eq(day)]
    all_symbols = (
        sorted(universe.symbol.unique())
        if universe is not None
        else sorted(screen.symbol.unique())
    )
    absent = sorted(set(all_symbols) - set(current.symbol))
    if absent:
        st.info(
            f"{len(absent)} selected stocks have no completed daily candle on {day}."
        )
        with st.expander("Stocks without a daily candle"):
            st.dataframe(
                pd.DataFrame(
                    {"symbol": absent, "reason": "No observed complete daily candle"}
                ),
                hide_index=True,
            )
    names = [
        name for name in result.settings["strategies"] if name + "_reason" in screen
    ]
    choice = st.selectbox("Screening strategy", names)
    state = st.radio(
        "Screening selection",
        ["All stocks", "Selected candidates", "Excluded stocks"],
        horizontal=True,
    )
    if state != "All stocks":
        selected = current[choice + "_selected"]
        current = current.loc[selected if state == "Selected candidates" else ~selected]
    symbols = st.multiselect("Screening stocks", all_symbols)
    if symbols:
        current = current.loc[current.symbol.isin(symbols)]
    preferred = [
        "timestamp",
        "symbol",
        "sector",
        "eligible",
        "eligibility_reason",
        choice + "_selected",
        choice + "_reason",
        "daily_history_sessions",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "avg_turnover",
        "avg_volume",
        "sma20",
        "sma50",
        "sma200",
        "ema20",
        "atr",
        "rs20",
        "rs60",
        "rs20_rank",
        "rs60_rank",
        "vol_rank",
        "atr_rank",
        "support",
        "breakout_high",
        "regime",
        "breadth",
        "adx",
    ]
    st.write(
        f"{len(current)} stock rows for {day}. Stocks without an observed daily candle on this date have no screening row."
    )
    st.dataframe(
        current[[c for c in preferred if c in current]],
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Download displayed daily screening",
        current.to_csv(index=False),
        f"screener_{day}.csv",
        "text/csv",
    )


def render_stock_trades(result, dataset, mode):
    st.subheader("Stock signals and trade details")
    signals = result.signal_log
    traded = sorted(result.trades.symbol.unique())
    available = sorted(dataset.universe.symbol.unique())
    symbol = st.selectbox("Stock", traded + [s for s in available if s not in traded])
    stock_trades = result.trades.loc[result.trades.symbol.eq(symbol)]
    stock_signals = signals.loc[signals.symbol.eq(symbol)]
    st.markdown("**Closed trades**")
    st.dataframe(stock_trades, hide_index=True, width="stretch")
    st.download_button(
        "Download stock trade ledger",
        stock_trades.to_csv(index=False),
        f"{symbol}_trades.csv",
        "text/csv",
    )
    st.markdown("**Every triggered signal and its order outcome**")
    st.dataframe(
        stock_signals.drop(columns=["features"]), hide_index=True, width="stretch"
    )
    st.download_button(
        "Download stock signals",
        signal_export(result, symbol=symbol).to_csv(index=False),
        f"{symbol}_signals.csv",
        "text/csv",
    )
    if stock_signals.empty:
        st.info(
            "No triggers for this stock. Use Daily screener and Run diagnostics to inspect its selection gates."
        )
        return
    lookup = stock_signals.set_index("signal_id")
    signal_id = st.selectbox(
        "Inspect triggered signal",
        list(lookup.index),
        format_func=lambda i: (
            f"#{i} · {lookup.loc[i, 'timestamp']} · {lookup.loc[i, 'strategy']} · {lookup.loc[i, 'side']} · {lookup.loc[i, 'status']}"
        ),
    )
    selected = lookup.loc[signal_id]
    st.markdown("**Signal-time indicator values**")
    st.dataframe(pd.DataFrame([selected.features]), hide_index=True, width="stretch")
    st.markdown("**Daily candle used for selection**")
    screen = result.daily_screener
    daily = screen.loc[
        screen.symbol.eq(symbol) & screen.timestamp.eq(selected.screen_timestamp)
    ]
    st.dataframe(daily, hide_index=True, width="stretch")
    candles = dataset.daily if mode == "daily" else dataset.intraday
    begin = selected.timestamp.normalize()
    end_time = (
        selected.exit_time if pd.notna(selected.exit_time) else selected.timestamp
    )
    finish = max(end_time, selected.timestamp).normalize() + pd.Timedelta(days=1)
    candles = candles.loc[
        candles.symbol.eq(symbol)
        & candles.timestamp.between(begin, finish, inclusive="left")
    ]
    fig = go.Figure(
        go.Candlestick(
            x=candles.timestamp,
            open=candles.open,
            high=candles.high,
            low=candles.low,
            close=candles.close,
            name="Execution candles",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[selected.timestamp],
            y=[selected.entry_price],
            mode="markers",
            name="Signal",
            marker=dict(color="orange", size=11),
        )
    )
    if selected.status == "filled":
        fig.add_trace(
            go.Scatter(
                x=[selected.execution_time],
                y=[selected.fill_price],
                mode="markers",
                name="Entry",
                marker=dict(color="#4dd8b7", size=11),
            )
        )
        trade = stock_trades.loc[stock_trades.signal_id.eq(signal_id)]
        if not trade.empty:
            fig.add_trace(
                go.Scatter(
                    x=trade.exit_time,
                    y=trade.exit_price,
                    mode="markers",
                    name="Exit",
                    marker=dict(color="#ee7186", size=11),
                )
            )
    fig.update_layout(
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        title=f"{symbol} · signal #{signal_id}",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Candles use close timestamps. A next-bar market fill uses that candle’s open; fees and slippage are included in the ledger. Swing triggers use daily closes and execute in 5-minute bars in hybrid mode."
    )
    st.dataframe(candles, hide_index=True, width="stretch")
    st.download_button(
        "Download trade execution candles",
        candles.to_csv(index=False),
        f"{symbol}_signal_{signal_id}_candles.csv",
        "text/csv",
    )
