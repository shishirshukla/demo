import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.converted_backtest import render_converted_backtest
from frontend.kite_backtest import render_saved_kite_backtest
from frontend.kite_download import capture_kite_callback, render_kite_download
from frontend.research_flow import (
    render_daily_screener,
    render_diagnostics,
    render_stock_trades,
)
from trading_system.backtest import BacktestEngine
from trading_system.backtest.metrics import metrics, period_returns
from trading_system.backtest.preparation import prepare_backtest
from trading_system.backtest.walk_forward import (
    evaluate_walk_forward,
    robustness,
    windows,
)
from trading_system.config import load_settings
from trading_system.data import demo_dataset
from trading_system.data.historical import Dataset
from trading_system.reports.performance import (
    breakdowns,
    overlap,
    portfolio_series,
    signal_export,
)

st.set_page_config(page_title="NIFTY · Regime Lab", page_icon="📈", layout="wide")
st.markdown(
    """<style>
.stApp {background:#0b1120;color:#e2e8f0;} [data-testid=stSidebar] {background:#111b2e;}
[data-testid=stMetric] {background:#142039;padding:18px;border-radius:12px;border:1px solid #253451;}
h1 {letter-spacing:-1px;} .label {color:#4dd8b7;font-size:12px;letter-spacing:3px;font-weight:700;}
</style>""",
    unsafe_allow_html=True,
)


@st.cache_data
def get_demo():
    return demo_dataset()


def chart(fig):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=12, r=12, t=40, b=12),
    )
    st.plotly_chart(fig, width="stretch")


def bundle(results, progress=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        summaries = []
        archive.writestr(
            "daily_screener.csv", results[0].daily_screener.to_csv(index=False)
        )
        for i, r in enumerate(results):
            if progress:
                progress(
                    i / len(results),
                    f"Exporting {r.slippage_bps:g} bps: trades, signals, diagnostics and portfolio reports",
                )
            prefix = f"{r.slippage_bps:g}bps"
            m = metrics(r)
            summaries.append({"slippage_bps": r.slippage_bps, **m})
            tables = {
                "trades": r.trades,
                "equity": r.equity,
                "regimes": r.regimes,
                "signals": signal_export(r),
                "trigger_diagnostics": r.diagnostics,
                "portfolio_daily": portfolio_series(r).reset_index(),
                "monthly_returns": period_returns(r, "ME").reset_index(),
                "yearly_returns": period_returns(r, "YE").reset_index(),
                "strategy_overlap": overlap(r).reset_index(),
                **{f"by_{k}": v for k, v in breakdowns(r).items()},
            }
            for name, table in tables.items():
                archive.writestr(f"{prefix}/{name}.csv", table.to_csv(index=False))
            archive.writestr(
                f"{prefix}/report.json",
                json.dumps(
                    {
                        "metrics": m,
                        "settings": r.settings,
                        "synthetic": r.synthetic,
                        "rejections": r.rejections,
                        "trades": json.loads(
                            r.trades.to_json(orient="records", date_format="iso")
                        ),
                    },
                    indent=2,
                    allow_nan=False,
                ),
            )
        archive.writestr(
            "slippage_comparison.csv", pd.DataFrame(summaries).to_csv(index=False)
        )
    if progress:
        progress(1.0, "Results ZIP ready")
    return buffer.getvalue()


capture_kite_callback()

with st.sidebar:
    st.markdown("### NIFTY / REGIME LAB")
    st.caption("Research workspace · NSE cash equities")
    page = st.radio(
        "Menu", ["Backtest dashboard", "Download Kite data"], key="workspace_menu"
    )
    if page == "Download Kite data":
        st.caption("Zerodha Kite - historical one-minute OHLCV")

if page == "Download Kite data":
    render_kite_download()
    st.stop()

with st.sidebar:
    source = st.radio(
        "Data source",
        [
            "Synthetic demo",
            "Generated Kite candles",
            "Saved Kite downloads",
            "Upload CSV files",
        ],
    )
    settings_file = st.file_uploader("Optional settings YAML", type=["yaml", "yml"])
    try:
        settings = load_settings(settings_file)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        st.error(f"Invalid settings: {exc}")
        st.stop()
    mode = st.selectbox(
        "Execution timeframe",
        ["daily", "intraday", "hybrid"],
        index=2,
        help="Hybrid executes both swing and intraday strategies using 5-minute fills.",
    )
    st.markdown("#### Strategies")
    for name, params in settings["strategies"].items():
        params["enabled"] = st.checkbox(
            name.replace("_", " ").title(),
            value=params["enabled"],
            key=f"enable_{name}",
        )
    st.markdown("#### Portfolio")
    settings["portfolio"]["initial_capital"] = st.number_input(
        "Starting capital (INR)",
        min_value=1000,
        value=int(settings["portfolio"]["initial_capital"]),
        step=10000,
    )
    settings["portfolio"]["risk_per_trade"] = (
        st.slider(
            "Risk per trade (%)",
            0.1,
            2.0,
            float(settings["portfolio"]["risk_per_trade"] * 100),
            0.1,
        )
        / 100
    )
    settings["portfolio"]["max_positions"] = st.slider(
        "Maximum positions", 1, 10, int(settings["portfolio"]["max_positions"])
    )
    dataset = None
    data_selection = {"source": source}
    if source == "Synthetic demo":
        demo_status = st.progress(
            0.0, text="Loading seeded synthetic daily and 5-minute candles"
        )
        dataset = get_demo()
        demo_status.progress(
            1.0,
            text="Synthetic data ready: daily warmup and 30 complete 5-minute sessions",
        )
    elif source == "Generated Kite candles":
        dataset, data_selection = render_converted_backtest(mode)
    elif source == "Saved Kite downloads":
        dataset, data_selection = render_saved_kite_backtest(mode)
    else:
        uploads = {
            key: st.file_uploader(label, type="csv", key=key)
            for key, label in [
                ("daily", "Daily OHLCV"),
                ("benchmark", "NIFTY 50 daily"),
                ("universe", "Universe membership"),
                ("intraday", "5-minute OHLCV (optional)"),
                ("benchmark_intraday", "NIFTY 50 5-minute (optional)"),
            ]
        }
        if all(uploads[key] is not None for key in ["daily", "benchmark", "universe"]):
            try:
                upload_status = st.progress(
                    0.0, text="Reading uploaded candles and universe"
                )
                frames = {}
                for i, (key, value) in enumerate(uploads.items()):
                    upload_status.progress(
                        i / len(uploads) * 0.9, text=f"Reading uploaded {key}"
                    )
                    frames[key] = pd.read_csv(value) if value else None
                upload_status.progress(
                    0.95, text="Validating daily and 5-minute data contracts"
                )
                dataset = Dataset(**frames).validate()
                upload_status.progress(1.0, text="Uploaded data ready")
            except Exception as exc:
                st.error(f"Cannot load data: {exc}")
    start, end = None, None
    if dataset:
        data_range = (
            dataset.daily
            if mode == "daily" or dataset.intraday is None
            else dataset.intraday
        )
        lo, hi = data_range.timestamp.min().date(), data_range.timestamp.max().date()
        selected = st.date_input(
            "Research period", value=(lo, hi), min_value=lo, max_value=hi
        )
        if len(selected) == 2:
            start, end = map(str, selected)
    if mode != "hybrid":
        st.info(
            "Hybrid runs all five strategies using daily screening and 5-minute execution. The selected mode skips the other timeframe’s strategies."
        )
    run = st.button(
        "Run backtest →", type="primary", width="stretch", disabled=dataset is None
    )
    st.caption(
        "Signal at close → fill on next bar. Slippage scenarios: 3 / 5 / 10 bps. No live broker connected."
    )

st.markdown(
    '<div class="label">SYSTEMATIC RESEARCH / INDIA</div>', unsafe_allow_html=True
)
st.title("Market regimes. Measured results.")
st.caption("Five strategies · one risk framework · reproducible backtests")
if run:
    try:
        status = st.progress(
            0.0, text="Preparing daily screener and 5-minute execution features"
        )
        prepared = prepare_backtest(
            dataset,
            settings,
            start,
            end,
            mode,
            progress=lambda f, m: status.progress(0.35 * f, text=m),
        )
        completed = []
        scenarios = settings["costs"]["slippage_scenarios"]
        for i, bps in enumerate(scenarios):
            completed.append(
                BacktestEngine(settings).run(
                    dataset,
                    bps,
                    start,
                    end,
                    mode,
                    prepared=prepared,
                    progress=lambda f, m, i=i: status.progress(
                        0.35 + 0.65 * (i + f) / len(scenarios), text=m
                    ),
                )
            )
        st.session_state["results"] = completed
        st.session_state["last_data_selection"] = data_selection
        st.session_state["last_dataset"] = dataset
        st.session_state["last_mode"] = mode
        st.session_state["last_range"] = (start, end)
        st.session_state.pop("results_zip", None)
        status.progress(
            1.0,
            text=f"Complete: {len(scenarios)} scenarios · {len(completed[0].daily_screener):,} daily screening rows",
        )
    except Exception as exc:
        st.error(f"Backtest failed: {exc}")

if "results" not in st.session_state:
    st.info(
        "Choose your data and strategies, then run a backtest. The demo contains fictional prices for eight symbols."
    )
    a, b, c = st.columns(3)
    a.metric("Capital base", "₹150,000")
    b.metric("Strategy classes", "5")
    c.metric("Slippage scenarios", "3 / 5 / 10 bps")
    st.markdown(
        "**Daily:** relative strength pullback and compression breakout. **Intraday:** weakness breakdown, VWAP reversion, and opening momentum."
    )
    st.stop()

results = st.session_state["results"]
if results[0].synthetic:
    st.warning(
        "SYNTHETIC DEMO — fictional symbols and prices. These results demonstrate software behavior, not strategy profitability."
    )
st.caption(
    f"Showing last completed run: {st.session_state['last_mode']} · {st.session_state['last_range'][0]} to {st.session_state['last_range'][1]}. Run again to apply changed controls."
)
last_selection = st.session_state.get("last_data_selection", {})
if last_selection.get("source") in ("Saved Kite downloads", "Generated Kite candles"):
    st.caption(
        "Last completed run used downloaded stocks: "
        + ", ".join(last_selection["symbols"])
        + " / benchmark "
        + last_selection["benchmark"]
    )
scenario_values = [x.slippage_bps for x in results]
scenario = st.selectbox(
    "Slippage scenario", scenario_values, index=1 if len(scenario_values) > 1 else 0
)
r = next(x for x in results if x.slippage_bps == scenario)
m = metrics(r)
columns = st.columns(5)
for column, label, value in zip(
    columns,
    ["Net return", "Max drawdown", "Sharpe", "Closed trades", "Transaction fees"],
    [
        f"{m['total_return']:.2%}",
        f"{m['max_drawdown']:.2%}",
        f"{m['sharpe']:.2f}",
        str(m["trades"]),
        f"₹{m['total_costs']:,.0f}",
    ],
):
    column.metric(label, value)
if st.button("Prepare results ZIP"):
    export_status = st.progress(0.0, text="Exporting daily screener and results")
    st.session_state["results_zip"] = bundle(
        results, lambda f, m: export_status.progress(f, text=m)
    )
if "results_zip" in st.session_state:
    st.download_button(
        "Download results · CSV + JSON ZIP",
        data=st.session_state["results_zip"],
        file_name="nifty_backtest.zip",
        mime="application/zip",
    )
screen = st.radio(
    "Result screen",
    [
        "Run diagnostics",
        "Daily screener",
        "Stock trade details",
        "Portfolio and research",
    ],
    horizontal=True,
)
if screen == "Run diagnostics":
    render_diagnostics(
        r, st.session_state["last_mode"], st.session_state["last_dataset"]
    )
    st.stop()
if screen == "Daily screener":
    render_daily_screener(r, st.session_state["last_dataset"].universe)
    st.stop()
if screen == "Stock trade details":
    render_stock_trades(
        r, st.session_state["last_dataset"], st.session_state["last_mode"]
    )
    st.stop()
overview, strategies, trades_tab, regimes_tab, research = st.tabs(
    [
        "Portfolio",
        "Strategy attribution",
        "Trade ledger",
        "Regimes & risk",
        "Validation",
    ]
)
with overview:
    series = portfolio_series(r).reset_index()
    chart(
        px.line(
            series,
            x="timestamp",
            y="equity",
            title="Portfolio equity · INR",
            color_discrete_sequence=["#4dd8b7"],
        )
    )
    left, right = st.columns(2)
    with left:
        chart(
            px.area(
                series,
                x="timestamp",
                y="drawdown",
                title="Drawdown",
                color_discrete_sequence=["#ee7186"],
            )
        )
    with right:
        chart(
            px.line(
                series,
                x="timestamp",
                y="capital_utilization",
                title="Capital utilization",
                color_discrete_sequence=["#80aaff"],
            )
        )
    st.markdown("**Cost sensitivity**")
    st.dataframe(
        pd.DataFrame([{"slippage_bps": x.slippage_bps, **metrics(x)} for x in results]),
        hide_index=True,
    )
    monthly = period_returns(r, "ME").reset_index()
    chart(
        px.bar(
            monthly,
            x="timestamp",
            y="return",
            title="Monthly returns",
            color="return",
            color_continuous_scale="RdYlGn",
        )
    )
    chart(
        px.line(
            series,
            x="timestamp",
            y="rolling_sharpe_60",
            title="60-session rolling Sharpe",
        )
    )
with strategies:
    tables = breakdowns(r)
    dimension = st.selectbox("Attribute performance by", list(tables))
    st.dataframe(tables[dimension], hide_index=True, width="stretch")
    if len(r.trades):
        chart(
            px.bar(
                tables["strategy"],
                x="strategy",
                y="net_pnl",
                title="Strategy net contribution",
                color="strategy",
            )
        )
        contributors = (
            r.trades.groupby("symbol", as_index=False)
            .net_pnl.sum()
            .sort_values("net_pnl")
        )
        chart(
            px.bar(
                contributors,
                x="symbol",
                y="net_pnl",
                title="Symbol concentration · all contributors",
                color="net_pnl",
                color_continuous_scale="RdYlGn",
            )
        )
        left, right = st.columns(2)
        left.dataframe(
            contributors.tail(10).sort_values("net_pnl", ascending=False),
            hide_index=True,
        )
        right.dataframe(contributors.head(10), hide_index=True)
    st.markdown(
        "**Strategy overlap · fraction of event observations simultaneously exposed**"
    )
    st.dataframe(overlap(r))
with trades_tab:
    filtered = r.trades
    choice = st.multiselect("Filter strategy", sorted(filtered.strategy.unique()))
    if choice:
        filtered = filtered[filtered.strategy.isin(choice)]
    st.dataframe(filtered, hide_index=True, width="stretch")
    st.download_button(
        "Download trade ledger", filtered.to_csv(index=False), "trades.csv", "text/csv"
    )
with regimes_tab:
    chart(
        px.scatter(
            r.regimes,
            x="timestamp",
            y="breadth",
            color="label",
            title="Regime and breadth",
            color_discrete_map={
                "BULL": "#4dd8b7",
                "BEAR": "#ee7186",
                "FLAT": "#80aaff",
            },
        )
    )
    st.markdown("**Rejected entries**")
    st.json(r.rejections)
    sector = pd.DataFrame(
        r.equity.sector_exposure.tolist(), index=r.equity.timestamp
    ).fillna(0)
    if len(sector.columns):
        chart(px.area(sector, title="Sector exposure · INR"))
    st.json(r.settings["portfolio"])
with research:
    st.caption(
        "Chronological validation with frozen parameters. The dashboard does not select an optimum or fit the final test period."
    )
    train_years = st.number_input("Training years", min_value=1, value=5)
    if st.button("Evaluate yearly walk-forward folds"):
        ds = st.session_state["last_dataset"]
        folds = list(
            windows(
                ds.daily.timestamp.min().date(),
                ds.daily.timestamp.max().date(),
                train_years=int(train_years),
            )
        )
        if not folds:
            st.info(
                "Not enough history for the selected window. Upload a longer daily history or shorten the training window."
            )
        else:
            try:
                with st.container():
                    validation_status = st.progress(
                        0.0, text="Evaluating chronological folds"
                    )
                    st.dataframe(
                        evaluate_walk_forward(
                            ds,
                            r.settings,
                            folds,
                            st.session_state["last_mode"],
                            progress=lambda f, m: validation_status.progress(f, text=m),
                        )
                    )
            except ValueError as exc:
                st.error(str(exc))
    st.markdown("**Parameter neighborhood · momentum stop ATR**")
    if st.button("Compare 1.5 / 2.0 / 2.5 ATR"):
        with st.container():
            neighborhood_status = st.progress(
                0.0, text="Evaluating parameter neighborhood"
            )
            ds = st.session_state["last_dataset"]
            st.dataframe(
                robustness(
                    ds,
                    r.settings,
                    "momentum_pullback",
                    "atr_stop",
                    [1.5, 2.0, 2.5],
                    *st.session_state["last_range"],
                    mode=st.session_state["last_mode"],
                    progress=lambda f, m: neighborhood_status.progress(f, text=m),
                )
            )
    st.caption(
        "Always reserve a final untouched period. A no-trade result is reported as such. Correlation controls, Monte Carlo analysis, and live state recovery are future extensions."
    )
