"""Dashboard selection for generated daily and five-minute candle files."""

from pathlib import Path

import pandas as pd
import streamlit as st

from trading_system.data.converted import (
    CONVERTED_ROOT,
    converted_catalog,
    converted_fingerprint,
    load_converted_dataset,
)


def render_converted_backtest(mode):
    entries = converted_catalog(CONVERTED_ROOT)
    if not entries:
        st.info(
            "No generated candles found. Run scripts/convert_kite_minutes.py first."
        )
        return None, None
    catalog = {str(e["path"]): e for e in entries}
    chosen = st.selectbox(
        "Generated candle dataset", list(catalog), format_func=lambda p: Path(p).name
    )
    available = catalog[chosen]["symbols"]
    indices = [
        s for s in available if s in ("NIFTY 50", "NIFTY BANK", "INDIA VIX", "NIFTY50")
    ]
    if not indices:
        st.error("The generated dataset needs a benchmark index, such as NIFTY 50.")
        return None, None
    symbols = st.multiselect(
        "Generated stocks for backtest",
        [s for s in available if s not in indices],
        default=[s for s in available if s not in indices],
    )
    benchmark = st.selectbox(
        "Benchmark index",
        indices,
        index=indices.index("NIFTY 50") if "NIFTY 50" in indices else 0,
    )
    if not symbols:
        return None, None
    sector_file = (
        Path(__file__).resolve().parents[1] / "config/universe_nse_example.csv"
    )
    sectors = pd.read_csv(sector_file).set_index("symbol").sector.to_dict()
    universe = pd.DataFrame(
        {"symbol": symbols, "sector": [sectors.get(s, "Unknown") for s in symbols]}
    )
    with st.expander("Selected universe / sectors"):
        st.caption(
            "Set sectors for portfolio limits. Stocks labelled Unknown share one sector limit."
        )
        universe = st.data_editor(
            universe, disabled=["symbol"], hide_index=True, key="converted_sectors"
        )
    try:
        if (
            universe.sector.isna().any()
            or universe.sector.astype(str).str.strip().eq("").any()
        ):
            raise ValueError("Set a sector for each stock")
        fingerprint = converted_fingerprint(chosen, [*symbols, benchmark], mode)
        key = (
            fingerprint,
            tuple(symbols),
            benchmark,
            mode,
            tuple(universe.itertuples(index=False, name=None)),
        )
        if st.button("Load selected data", key="load_converted"):
            status = st.progress(0.0, text="Loading generated candles")
            dataset, coverage = load_converted_dataset(
                chosen,
                symbols,
                benchmark,
                mode=mode,
                universe=universe,
                progress=lambda f, m: status.progress(f, text=m),
            )
            if fingerprint != converted_fingerprint(
                chosen, [*symbols, benchmark], mode
            ):
                raise ValueError("Generated files changed during loading. Load again.")
            st.session_state["converted_prepared"] = {
                "key": key,
                "dataset": dataset,
                "coverage": coverage,
            }
        prepared = st.session_state.get("converted_prepared")
        if prepared is None or prepared["key"] != key:
            st.info("Click Load selected data to enable the backtest.")
            return None, None
        coverage = prepared["coverage"]
        st.caption(
            f"{len(symbols)} stocks · {coverage['available_sessions']} screening sessions · {coverage['first_session']} to {coverage['last_session']}"
        )
        with st.expander("Prepared data coverage"):
            st.dataframe(pd.DataFrame(coverage["per_symbol"]), hide_index=True)
            st.caption(coverage["session_policy"])
        return prepared["dataset"], {
            "source": "Generated Kite candles",
            "folders": [chosen],
            "symbols": symbols,
            "benchmark": benchmark,
            "coverage": coverage,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Cannot load generated candles: {exc}")
        return None, None
