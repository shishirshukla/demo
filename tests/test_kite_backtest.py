import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from trading_system.data.kite_backtest import (
    load_kite_candles,
    prepare_kite_backtest,
    prepare_saved_kite_backtest,
    saved_kite_datasets,
)
from trading_system.data.kite_downloader import KiteDownload, save_kite_download


def fixture_download(root):
    frames = []
    for symbol, token in [("RELIANCE", 1), ("TCS", 2), ("NIFTY 50", 3)]:
        for day in ["2026-09-01", "2026-09-02", "2026-09-03"]:
            timestamps = pd.date_range(
                day + " 09:15", periods=375, freq="min", tz="Asia/Kolkata"
            )
            frames.append(
                pd.DataFrame(
                    {
                        "timestamp": timestamps,
                        "symbol": symbol,
                        "instrument_token": token,
                        "exchange": "NSE",
                        "open": 100 + token,
                        "high": 105 + token,
                        "low": 98 + token,
                        "close": 102 + token,
                        "volume": 10,
                    }
                )
            )
    candles = pd.concat(frames, ignore_index=True)
    instruments = pd.DataFrame(
        {
            "tradingsymbol": ["RELIANCE", "TCS", "NIFTY 50"],
            "instrument_token": [1, 2, 3],
            "exchange": "NSE",
            "segment": ["NSE", "NSE", "INDICES"],
        }
    )
    manifest = {
        "provider": "Zerodha Kite Connect",
        "interval": "minute",
        "timestamp_convention": "bar start (Kite native)",
        "coverage": [{"symbol": s} for s in instruments.tradingsymbol],
    }
    folder = save_kite_download(
        KiteDownload(candles, instruments, manifest), "fixture", root=root
    )
    return folder, candles


def test_prepares_only_selected_stock_and_correct_bars(tmp_path):
    folder, _ = fixture_download(tmp_path)
    candles, _ = load_kite_candles([folder])
    dataset, coverage = prepare_kite_backtest(
        candles, ["TCS"], "NIFTY 50", mode="intraday"
    )
    assert set(dataset.daily.symbol) == {"TCS"}
    assert set(dataset.universe.symbol) == {"TCS"}
    assert dataset.benchmark.symbol.unique().tolist() == ["NIFTY 50"]
    assert len(dataset.daily) == 3 and coverage["common_sessions"] == 3
    assert dataset.daily.volume.tolist() == [3750] * 3
    assert dataset.daily.turnover.tolist() == [104 * 3750] * 3
    assert len(dataset.intraday) == 225
    assert dataset.intraday.timestamp.iloc[0].strftime("%H:%M") == "09:20"
    assert dataset.intraday.timestamp.iloc[-1].strftime("%H:%M") == "15:30"
    assert dataset.intraday.volume.tolist() == [50] * 225
    assert not dataset.synthetic


def test_incomplete_session_dropped_only_for_affected_symbol(tmp_path):
    _, candles = fixture_download(tmp_path)
    candles = candles.drop(
        candles.loc[
            (candles.symbol == "TCS")
            & (candles.timestamp.dt.strftime("%Y-%m-%d %H:%M") == "2026-09-02 10:13")
        ].index
    )
    dataset, coverage = prepare_kite_backtest(
        candles, ["TCS", "RELIANCE"], "NIFTY 50", mode="hybrid"
    )
    assert coverage["common_sessions"] == 2
    assert len(coverage["excluded_incomplete_sessions"]) == 1
    assert set(dataset.daily.timestamp.dt.strftime("%Y-%m-%d")) == {
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
    }
    assert dataset.daily.groupby("symbol").size().to_dict() == {"RELIANCE": 3, "TCS": 2}
    assert coverage["available_sessions"] == 3
    assert len(dataset.intraday) == 375
    assert len(dataset.benchmark_intraday) == 225


def test_overlaps_deduplicated_conflicts_rejected(tmp_path):
    folder, _ = fixture_download(tmp_path)
    candles, _ = load_kite_candles([folder, folder])
    assert len(candles) == 3375
    other, _ = fixture_download(tmp_path)
    changed = pd.read_csv(other / "candles_1minute.csv")
    changed.loc[0, "close"] = 100
    changed.to_csv(other / "candles_1minute.csv", index=False)
    with pytest.raises(ValueError, match="conflicting candles"):
        load_kite_candles([folder, other])


def test_catalog_and_bad_source(tmp_path):
    folder, _ = fixture_download(tmp_path)
    assert saved_kite_datasets(tmp_path)[0]["path"] == folder
    manifest = json.loads((folder / "download_manifest.json").read_text())
    manifest["interval"] = "5minute"
    (folder / "download_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="one-minute bar-start"):
        load_kite_candles([folder])


def test_no_complete_overlap_and_no_selection(tmp_path):
    _, candles = fixture_download(tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        prepare_kite_backtest(candles, [], "NIFTY 50")
    with pytest.raises(ValueError, match="cannot also"):
        prepare_kite_backtest(candles, ["NIFTY 50"], "NIFTY 50")
    with pytest.raises(ValueError, match="No common complete"):
        prepare_kite_backtest(
            candles.groupby("symbol").head(20), ["RELIANCE"], "NIFTY 50"
        )


def test_dashboard_runs_only_selected_downloaded_symbol(tmp_path, monkeypatch):
    import frontend.kite_backtest as menu

    fixture_download(tmp_path)
    monkeypatch.setattr(menu, "STORAGE_ROOT", tmp_path)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Data source").set_value(
        "Saved Kite downloads"
    ).run()
    assert not app.exception and not app.error
    selector = next(
        m for m in app.multiselect if m.label == "Downloaded symbols for backtest"
    )
    assert selector.options == ["RELIANCE", "TCS"]
    selector.set_value(["TCS"]).run()
    next(b for b in app.button if b.label == "Load selected data").click().run()
    next(b for b in app.button if b.label.startswith("Run backtest")).click().run(
        timeout=120
    )
    assert not app.exception and not app.error
    assert app.session_state["last_data_selection"]["symbols"] == ["TCS"]
    assert app.session_state["last_dataset"].daily.symbol.unique().tolist() == ["TCS"]
    assert len(app.session_state["results"]) == 3
    assert not app.session_state["results"][0].synthetic


def test_no_downloads_disables_backtest(tmp_path, monkeypatch):
    import frontend.kite_backtest as menu

    monkeypatch.setattr(menu, "STORAGE_ROOT", tmp_path)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Data source").set_value(
        "Saved Kite downloads"
    ).run()
    assert not app.exception
    assert next(b for b in app.button if b.label.startswith("Run backtest")).disabled


@pytest.mark.parametrize("mode", ["daily", "intraday", "hybrid"])
def test_streamed_preparation_matches_in_memory_for_unordered_chunks(tmp_path, mode):
    folder, _ = fixture_download(tmp_path)
    # Sessions and symbols cross chunk boundaries and need not be sorted on disk.
    path = folder / "candles_1minute.csv"
    frame = pd.read_csv(path).sample(frac=1, random_state=1)
    frame.to_csv(path, index=False)
    candles, _ = load_kite_candles([folder])
    expected, expected_coverage = prepare_kite_backtest(
        candles, ["TCS", "RELIANCE"], "NIFTY 50", mode=mode
    )
    updates = []
    actual, coverage = prepare_saved_kite_backtest(
        [folder],
        ["TCS", "RELIANCE"],
        "NIFTY 50",
        mode=mode,
        chunksize=400,
        progress=lambda fraction, message: updates.append(fraction),
    )
    for name in ("daily", "benchmark", "universe", "intraday", "benchmark_intraday"):
        if getattr(expected, name) is None:
            assert getattr(actual, name) is None
        else:
            pd.testing.assert_frame_equal(
                getattr(actual, name), getattr(expected, name)
            )
    assert coverage == expected_coverage
    assert updates == sorted(updates) and updates[-1] == 1.0


def test_streamed_overlaps_are_deduplicated_and_conflicts_rejected(tmp_path):
    folder, _ = fixture_download(tmp_path)
    other, _ = fixture_download(tmp_path)
    # Duplicate rows can also cross chunks within a single file.
    path = other / "candles_1minute.csv"
    frame = pd.read_csv(path)
    pd.concat([frame, frame.iloc[:1]], ignore_index=True).to_csv(path, index=False)
    dataset, coverage = prepare_saved_kite_backtest(
        [folder, other], ["TCS", "RELIANCE"], "NIFTY 50", chunksize=400
    )
    assert len(dataset.daily) == 6
    assert dataset.daily.volume.tolist() == [3750] * 6
    assert coverage["common_sessions"] == 3
    frame.loc[0, "close"] = 100
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="conflicting candles"):
        prepare_saved_kite_backtest(
            [folder, other], ["RELIANCE"], "NIFTY 50", chunksize=400
        )


def test_streamed_preparation_combines_partial_sessions_and_drops_missing_minutes(
    tmp_path,
):
    folder, _ = fixture_download(tmp_path)
    other, _ = fixture_download(tmp_path)
    candles = pd.read_csv(folder / "candles_1minute.csv")
    # One missing minute invalidates this stock session, preserving the other stock.
    candles = candles.drop(
        candles.loc[
            (candles.symbol == "TCS")
            & candles.timestamp.str.startswith("2026-09-02 10:13")
        ].index
    )
    candles.iloc[::2].to_csv(folder / "candles_1minute.csv", index=False)
    candles.iloc[1::2].to_csv(other / "candles_1minute.csv", index=False)
    dataset, coverage = prepare_saved_kite_backtest(
        [folder, other], ["TCS", "RELIANCE"], "NIFTY 50", mode="hybrid", chunksize=400
    )
    assert coverage["common_sessions"] == 2
    assert coverage["excluded_incomplete_sessions"] == [
        {"symbol": "TCS", "session": "2026-09-02", "minutes": 374}
    ]
    assert len(dataset.intraday) == 375
    assert len(dataset.benchmark_intraday) == 225


@pytest.mark.parametrize("invalid", ["timestamp", "high", "volume"])
def test_streamed_preparation_validates_selected_candles(tmp_path, invalid):
    folder, _ = fixture_download(tmp_path)
    path = folder / "candles_1minute.csv"
    frame = pd.read_csv(path)
    frame.loc[0, invalid] = {
        "timestamp": "2026-09-01 09:15:30+05:30",
        "high": 1,
        "volume": -1,
    }[invalid]
    frame.to_csv(path, index=False)
    # Invalid candles for an unselected stock do not prevent loading another stock.
    dataset, _ = prepare_saved_kite_backtest(
        [folder], ["TCS"], "NIFTY 50", chunksize=400
    )
    assert set(dataset.daily.symbol) == {"TCS"}
    with pytest.raises(ValueError, match="minute-aligned|invalid"):
        prepare_saved_kite_backtest([folder], ["RELIANCE"], "NIFTY 50", chunksize=400)


def test_dashboard_defers_loading_and_reuses_prepared_data(tmp_path, monkeypatch):
    import frontend.kite_backtest as menu

    folder, _ = fixture_download(tmp_path)
    monkeypatch.setattr(menu, "STORAGE_ROOT", tmp_path)
    preparations = []
    csv_reads = []
    original_prepare = menu.prepare_saved_kite_backtest
    original_read_csv = pd.read_csv

    def prepare(*args, **kwargs):
        preparations.append(args)
        return original_prepare(*args, **kwargs)

    def read_csv(source, *args, **kwargs):
        if Path(getattr(source, "name", source)).name == "candles_1minute.csv":
            csv_reads.append(kwargs.get("chunksize"))
        return original_read_csv(source, *args, **kwargs)

    monkeypatch.setattr(menu, "prepare_saved_kite_backtest", prepare)
    monkeypatch.setattr(pd, "read_csv", read_csv)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Data source").set_value(
        "Saved Kite downloads"
    ).run()
    assert not app.exception and not app.error
    assert not preparations and not csv_reads
    assert next(b for b in app.button if b.label.startswith("Run backtest")).disabled
    next(b for b in app.button if b.label == "Load selected data").click().run()
    assert not app.exception and not app.error
    assert len(preparations) == 1 and csv_reads == [100_000]
    assert not next(
        b for b in app.button if b.label.startswith("Run backtest")
    ).disabled
    app.checkbox[0].uncheck().run()
    assert len(preparations) == 1 and len(csv_reads) == 1
    next(
        m for m in app.multiselect if m.label == "Downloaded symbols for backtest"
    ).set_value(["TCS"]).run()
    assert len(preparations) == 1
    assert next(b for b in app.button if b.label.startswith("Run backtest")).disabled
    next(b for b in app.button if b.label == "Load selected data").click().run()
    assert not app.exception and not app.error
    assert len(preparations) == 2
    # A source update invalidates the prepared result without silently rescanning it.
    manifest = folder / "download_manifest.json"
    manifest.write_text(manifest.read_text() + "\n")
    app.run()
    assert not app.exception and not app.error
    assert len(preparations) == 2
    assert next(b for b in app.button if b.label.startswith("Run backtest")).disabled
