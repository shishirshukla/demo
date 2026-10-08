import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_system.data.kite_conversion import convert_kite_minutes


def minute_source(root):
    folder = root / "source"
    folder.mkdir()
    frames = []
    for symbol in ["RELIANCE", "TCS", "NIFTY 50"]:
        for day in ["2026-09-01", "2026-09-02"]:
            prices = 100 + np.arange(375) * 0.01
            frames.append(
                pd.DataFrame(
                    {
                        "timestamp": pd.date_range(
                            day + " 09:15", periods=375, freq="min", tz="Asia/Kolkata"
                        ),
                        "symbol": symbol,
                        "open": prices,
                        "high": prices + 1,
                        "low": prices - 1,
                        "close": prices + 0.02,
                        "volume": np.arange(375) + 1,
                    }
                )
            )
    frame = pd.concat(frames, ignore_index=True)
    frame.sample(frac=1, random_state=42).to_csv(
        folder / "candles_1minute.csv", index=False
    )
    return folder, frame


def test_script_writes_each_symbol_and_correct_ohlcv(tmp_path):
    source, _ = minute_source(tmp_path)
    output = tmp_path / "converted"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1] / "scripts/convert_kite_minutes.py"
            ),
            "--input",
            str(source),
            "--output",
            str(output),
            "--chunksize",
            "113",
            "--include-minute",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Converted 3 symbols" in result.stdout
    expected_files = {"RELIANCE.csv", "TCS.csv", "NIFTY_50.csv"}
    for folder in ["5minute", "daily", "1minute"]:
        assert {p.name for p in (output / folder).iterdir()} == expected_files
    five = pd.read_csv(output / "5minute/RELIANCE.csv")
    assert len(five) == 150 and five.symbol.unique().tolist() == ["RELIANCE"]
    assert five.timestamp.iloc[0] == "2026-09-01 09:20:00+05:30"
    assert five.timestamp.iloc[-1] == "2026-09-02 15:30:00+05:30"
    assert five.open.iloc[0] == 100
    assert five.high.iloc[0] == pytest.approx(101.04)
    assert five.low.iloc[0] == 99
    assert five.close.iloc[0] == pytest.approx(100.06)
    assert five.volume.iloc[0] == 15
    daily = pd.read_csv(output / "daily/RELIANCE.csv")
    assert len(daily) == 2
    assert daily.timestamp.iloc[0] == "2026-09-01 15:30:00+05:30"
    assert daily.open.iloc[0] == 100
    assert daily.high.iloc[0] == pytest.approx(104.74)
    assert daily.low.iloc[0] == 99
    assert daily.close.iloc[0] == pytest.approx(103.76)
    assert daily.volume.iloc[0] == sum(range(1, 376))
    expected_turnover = (
        (100 + np.arange(375) * 0.01 + 0.02) * (np.arange(375) + 1)
    ).sum()
    assert daily.turnover.iloc[0] == pytest.approx(expected_turnover)
    minutes = pd.read_csv(output / "1minute/RELIANCE.csv")
    assert len(minutes) == 750
    manifest = json.loads((output / "conversion_manifest.json").read_text())
    assert len(manifest["coverage"]) == 3
    assert all(not row["incomplete_five_minute_bins"] for row in manifest["coverage"])


def test_incomplete_bins_and_sessions_excluded_independently_per_symbol(tmp_path):
    source, frame = minute_source(tmp_path)
    missing = (frame.symbol == "TCS") & (
        frame.timestamp == pd.Timestamp("2026-09-02 10:12", tz="Asia/Kolkata")
    )
    extra = frame.iloc[:2].copy()
    extra["timestamp"] = pd.to_datetime(
        ["2026-09-01 09:14+05:30", "2026-09-01 15:30+05:30"]
    )
    pd.concat([frame.loc[~missing], extra]).to_csv(
        source / "candles_1minute.csv", index=False
    )
    output, manifest = convert_kite_minutes(
        [source], tmp_path / "converted", chunksize=113
    )
    assert not (output / "1minute").exists()
    assert len(pd.read_csv(output / "5minute/TCS.csv")) == 149
    assert len(pd.read_csv(output / "daily/TCS.csv")) == 1
    assert len(pd.read_csv(output / "daily/RELIANCE.csv")) == 2
    assert len(pd.read_csv(output / "daily/NIFTY_50.csv")) == 2
    coverage = {row["symbol"]: row for row in manifest["coverage"]}
    assert coverage["RELIANCE"]["excluded_out_of_session_rows"] == 2
    assert coverage["TCS"]["incomplete_five_minute_bins"] == [
        {"timestamp": "2026-09-02T10:15:00+05:30", "minutes": 4}
    ]
    assert coverage["TCS"]["excluded_incomplete_sessions"] == [
        {"symbol": "TCS", "session": "2026-09-02", "minutes": 374}
    ]


def test_combines_sources_deduplicates_and_rejects_conflicts_atomically(tmp_path):
    source, frame = minute_source(tmp_path)
    other = tmp_path / "other.csv"
    frame.iloc[:200].to_csv(other, index=False)
    output, _ = convert_kite_minutes(
        [source, other], tmp_path / "converted", chunksize=113
    )
    assert len(pd.read_csv(output / "5minute/RELIANCE.csv")) == 150
    assert pd.read_csv(output / "daily/RELIANCE.csv").volume.iloc[0] == sum(
        range(1, 376)
    )
    frame.loc[0, "close"] = 100.5
    frame.iloc[:200].to_csv(other, index=False)
    failed_output = tmp_path / "conflicting"
    with pytest.raises(ValueError, match="conflicting candles"):
        convert_kite_minutes([source, other], failed_output, chunksize=113)
    assert not failed_output.exists()
    assert not list(tmp_path.glob(".conversion_*"))


def test_naive_timestamps_are_interpreted_in_kolkata_and_symbol_filter_applies(
    tmp_path,
):
    source, frame = minute_source(tmp_path)
    frame["timestamp"] = frame.timestamp.dt.tz_localize(None)
    frame.to_csv(source / "candles_1minute.csv", index=False)
    output, manifest = convert_kite_minutes(
        [source], tmp_path / "converted", symbols=["TCS"]
    )
    assert [row["symbol"] for row in manifest["coverage"]] == ["TCS"]
    five = pd.read_csv(output / "5minute/TCS.csv")
    assert five.timestamp.iloc[0] == "2026-09-01 09:20:00+05:30"
    assert {p.name for p in (output / "daily").iterdir()} == {"TCS.csv"}


@pytest.mark.parametrize(
    "column,value",
    [("volume", -1), ("high", 1), ("timestamp", "2026-09-01 09:15:30+05:30")],
)
def test_invalid_input_never_publishes_partial_results(tmp_path, column, value):
    source, frame = minute_source(tmp_path)
    if column == "timestamp":
        frame["timestamp"] = frame.timestamp.astype(str)
    frame.loc[0, column] = value
    frame.to_csv(source / "candles_1minute.csv", index=False)
    output = tmp_path / "converted"
    with pytest.raises(ValueError, match="invalid|minute-aligned"):
        convert_kite_minutes([source], output, chunksize=113)
    assert not output.exists()
    assert not list(tmp_path.glob(".conversion_*"))


def test_existing_output_is_preserved_and_missing_symbols_rejected(tmp_path):
    source, _ = minute_source(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("original")
    with pytest.raises(FileExistsError, match="already exists"):
        convert_kite_minutes([source], output)
    assert marker.read_text() == "original"
    with pytest.raises(ValueError, match="Missing downloaded candles"):
        convert_kite_minutes([source], tmp_path / "missing", symbols=["MISSING"])
    with pytest.raises(ValueError, match="Chunk size must be positive"):
        convert_kite_minutes([source], tmp_path / "bad_chunk", chunksize=0)


def test_filename_collisions_are_rejected_without_overwriting(tmp_path):
    source, frame = minute_source(tmp_path)
    frame["symbol"] = frame.symbol.replace({"RELIANCE": "ABC/DEF", "TCS": "ABC_DEF"})
    frame.to_csv(source / "candles_1minute.csv", index=False)
    output = tmp_path / "converted"
    with pytest.raises(ValueError, match="same output filename"):
        convert_kite_minutes([source], output)
    assert not output.exists()


def test_empty_input_is_rejected_without_creating_outputs(tmp_path):
    source = tmp_path / "empty.csv"
    pd.DataFrame(
        columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    ).to_csv(source, index=False)
    with pytest.raises(ValueError, match="No minute candles"):
        convert_kite_minutes([source], tmp_path / "converted")
    assert not (tmp_path / "converted").exists()
