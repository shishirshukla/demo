"""Choose saved downloads and symbols for the backtest dashboard."""

from pathlib import Path

import pandas as pd
import streamlit as st

from trading_system.data.kite_backtest import (
    prepare_saved_kite_backtest,
    saved_kite_datasets,
)
from trading_system.data.kite_downloader import STORAGE_ROOT


def download_fingerprints(paths):
    return tuple(
        (
            path,
            *(
                (stat.st_mtime_ns, stat.st_size)
                for name in (
                    "candles_1minute.csv",
                    "instruments.csv",
                    "download_manifest.json",
                )
                for stat in [(Path(path) / name).stat()]
            ),
        )
        for path in paths
    )


def render_saved_kite_backtest(mode):
    entries = saved_kite_datasets(STORAGE_ROOT)
    if not entries:
        st.info(
            "No saved Kite downloads. Open Download Kite data and download stocks plus NIFTY 50 for the same dates."
        )
        return None, None
    catalog = {str(entry["path"]): entry for entry in entries}
    chosen = st.multiselect(
        "Saved Kite datasets",
        list(catalog),
        default=[next(iter(catalog))],
        format_func=lambda p: (
            f"{Path(p).name} - {len(catalog[p]['symbols'])} instruments"
        ),
        help="Choose multiple downloads to combine stocks, benchmark, and warmup history. Identical overlapping candles are deduplicated.",
    )
    if not chosen:
        st.info("Select a saved dataset to see its downloaded symbols.")
        return None, None
    try:
        fingerprints = download_fingerprints(chosen)
        # Coverage and instrument metadata are small, even for multi-GB downloads.
        # Do not read minute candles just to populate the selection widgets.
        available = sorted({s for p in chosen for s in catalog[p]["symbols"]})
        instruments = pd.concat(
            [pd.read_csv(Path(p) / "instruments.csv") for p in chosen],
            ignore_index=True,
        )
        index_mask = (
            instruments.get("segment", pd.Series("", index=instruments.index))
            .fillna("")
            .str.contains("INDICES", case=False)
        )
        indices = set(instruments.loc[index_mask, "tradingsymbol"]) | {
            "NIFTY 50",
            "NIFTY BANK",
            "INDIA VIX",
        }
        index_symbols = [s for s in available if s in indices]
        stocks = [s for s in available if s not in indices]
        symbols = st.multiselect(
            "Downloaded symbols for backtest",
            stocks,
            default=stocks,
            help="Only the selected stocks contribute signals and portfolio results.",
        )
        if not index_symbols:
            st.warning(
                "These downloads have no index benchmark. Download NIFTY 50 over the same dates and add that saved dataset above."
            )
            return None, None
        benchmark = st.selectbox(
            "Benchmark index",
            index_symbols,
            index=index_symbols.index("NIFTY 50") if "NIFTY 50" in index_symbols else 0,
        )
        if not symbols:
            st.info("Select at least one downloaded stock.")
            return None, None
        sectors = {}
        example = (
            Path(__file__).resolve().parents[1] / "config" / "universe_nse_example.csv"
        )
        if example.exists():
            sectors = pd.read_csv(example).set_index("symbol").sector.to_dict()
        universe = pd.DataFrame(
            {"symbol": symbols, "sector": [sectors.get(s, "Unknown") for s in symbols]}
        )
        with st.expander("Selected universe / sectors"):
            st.caption(
                "This selection is a fixed research universe. Set sectors for portfolio limits; Unknown groups stocks into one sector."
            )
            universe = st.data_editor(
                universe,
                disabled=["symbol"],
                hide_index=True,
                key="kite_backtest_sectors",
            )
        if (
            universe.sector.isna().any()
            or universe.sector.astype(str).str.strip().eq("").any()
        ):
            raise ValueError("Set a sector for each selected stock")
        preparation_key = (
            fingerprints,
            tuple(symbols),
            benchmark,
            mode,
            tuple(universe[["symbol", "sector"]].itertuples(index=False, name=None)),
        )
        if st.button(
            "Load selected data",
            help="Prepare the selected stocks and benchmark for backtesting.",
        ):
            status = st.progress(0, text="Reading saved Kite data...")
            try:
                dataset, coverage = prepare_saved_kite_backtest(
                    chosen,
                    symbols,
                    benchmark,
                    mode=mode,
                    universe=universe,
                    progress=lambda fraction, message: status.progress(
                        fraction, text=message
                    ),
                )
                # Reject a result if a source changed during preparation.
                if download_fingerprints(chosen) != fingerprints:
                    raise ValueError(
                        "Saved files changed while loading. Load the selected data again."
                    )
                st.session_state["kite_backtest_prepared"] = {
                    "key": preparation_key,
                    "dataset": dataset,
                    "coverage": coverage,
                }
            finally:
                status.empty()
        prepared = st.session_state.get("kite_backtest_prepared")
        if prepared is None or prepared["key"] != preparation_key:
            st.info(
                "Click Load selected data to set the research period and enable the backtest."
            )
            return None, None
        dataset, coverage = prepared["dataset"], prepared["coverage"]
        st.caption(
            f"{len(symbols)} stocks / {benchmark} / {coverage['available_sessions']} screening sessions / {coverage['first_session']} to {coverage['last_session']}"
        )
        if (
            coverage["excluded_incomplete_sessions"]
            or coverage["complete_sessions_outside_common_range"]
        ):
            st.warning(
                "Incomplete stock sessions are excluded individually. Other stocks retain their valid history; missing bars are never filled."
            )
        if any(not row["warmup_200_ready"] for row in coverage["per_symbol"]):
            st.warning(
                "Some stocks have fewer than 200 daily sessions. Strategies needing daily warmup may produce no trades; add earlier downloads for warmup."
            )
        st.caption(
            "Kite minute candles are aggregated into daily bars and five-minute bar-close candles. Provider prices are used as downloaded."
        )
        with st.expander("Prepared data coverage"):
            st.json(coverage)
        return dataset, {
            "source": "Saved Kite downloads",
            "folders": chosen,
            "symbols": symbols,
            "benchmark": benchmark,
            "coverage": coverage,
        }
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"Cannot prepare downloaded data: {exc}")
        return None, None
