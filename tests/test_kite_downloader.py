import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from trading_system.data.kite_downloader import (
    date_windows,
    download_kite_minutes,
    normalize_candles,
    save_kite_download,
)

INSTRUMENT = {
    "instrument_token": 738561,
    "tradingsymbol": "RELIANCE",
    "exchange": "NSE",
    "name": "Reliance",
}


def candle(clock="2026-10-05 09:15", **changes):
    return {
        "date": pd.Timestamp(clock, tz="Asia/Kolkata").to_pydatetime(),
        "open": 100,
        "high": 105,
        "low": 98,
        "close": 102,
        "volume": 123,
        **changes,
    }


class FakeKite:
    def __init__(self):
        self.calls = []
        self.token = None

    def instruments(self, exchange):
        assert exchange == "NSE"
        return [INSTRUMENT]

    def profile(self):
        return {"user_id": "TEST123"}

    def set_access_token(self, token):
        self.token = token

    def generate_session(self, request, api_secret):
        assert request == "one_time_request"
        assert api_secret == "private_secret"
        return {"access_token": "generated_token"}

    def historical_data(self, token, first, last, interval, **kwargs):
        self.calls.append((token, first, last, interval, kwargs))
        assert first.tzinfo is None and last.tzinfo is None
        stamp = pd.Timestamp(first).normalize() + pd.Timedelta(hours=9, minutes=15)
        return [candle(str(stamp))]


def instruments():
    return pd.DataFrame([INSTRUMENT])


def test_batch_ranges_are_disjoint_and_keep_kite_timestamps():
    client = FakeKite()
    result = download_kite_minutes(
        client,
        instruments(),
        "2026-07-01",
        "2026-09-30",
        now="2026-10-05 10:00",
        sleep=lambda _: None,
    )
    assert len(client.calls) == 4
    assert all(
        c[3] == "minute" and c[4] == {"continuous": False, "oi": False}
        for c in client.calls
    )
    for previous, following in zip(client.calls, client.calls[1:]):
        assert previous[2] + pd.Timedelta(seconds=1) == following[1]
    assert client.calls[-1][2] == datetime(2026, 9, 30, 23, 59, 59)
    assert result.candles.timestamp.dt.strftime("%H:%M").unique().tolist() == ["09:15"]
    assert result.candles.volume.tolist() == [123] * 4
    assert len(result.manifest["coverage"]) == 1


def test_today_excludes_live_candle_and_preserves_gaps():
    first, stop = date_windows("2026-10-05", "2026-10-05", now="2026-10-05 09:18:45")[0]
    frame = normalize_candles(
        [candle(), candle("2026-10-05 09:17"), candle("2026-10-05 09:18")],
        INSTRUMENT,
        first,
        stop,
    )
    assert frame.timestamp.dt.strftime("%H:%M").tolist() == ["09:15", "09:17"]
    assert stop.hour == 9 and stop.minute == 18


@pytest.mark.parametrize(
    "start,end", [("2026-10-06", "2026-10-06"), ("2026-10-04", "2026-10-03")]
)
def test_invalid_dates(start, end):
    with pytest.raises(ValueError):
        date_windows(start, end, now="2026-10-05 12:00")


@pytest.mark.parametrize(
    "change", [{"volume": -1}, {"high": 99}, {"close": float("nan")}, {"date": "bad"}]
)
def test_bad_candles_rejected(change):
    with pytest.raises(ValueError):
        normalize_candles(
            [candle(**change)],
            INSTRUMENT,
            pd.Timestamp("2026-10-05", tz="Asia/Kolkata"),
            pd.Timestamp("2026-10-06", tz="Asia/Kolkata"),
        )


def test_retries_and_auth_failure_does_not_retry():
    from kiteconnect.exceptions import NetworkException, TokenException

    client = FakeKite()
    original = client.historical_data
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise NetworkException("private response")
        return original(*args, **kwargs)

    client.historical_data = flaky
    delays = []
    download_kite_minutes(
        client,
        instruments(),
        "2026-10-05",
        "2026-10-05",
        now="2026-10-05 12:00",
        sleep=delays.append,
    )
    assert len(attempts) == 3 and delays == [0.4, 2, 4]

    def expired(*args, **kwargs):
        raise TokenException("secret-value-that-must-not-be-shown")

    client.historical_data = expired
    delays.clear()
    with pytest.raises(RuntimeError, match="session expired") as error:
        download_kite_minutes(
            client,
            instruments(),
            "2026-10-05",
            "2026-10-05",
            now="2026-10-05 12:00",
            sleep=delays.append,
        )
    assert "secret-value" not in str(error.value)
    assert delays == [0.4]


def test_empty_and_conflicting_candles_abort():
    client = FakeKite()
    client.historical_data = lambda *args, **kwargs: []
    with pytest.raises(ValueError, match="No completed candles"):
        download_kite_minutes(
            client,
            instruments(),
            "2026-10-05",
            "2026-10-05",
            now="2026-10-05 12:00",
            sleep=lambda _: None,
        )
    client.historical_data = lambda *args, **kwargs: [candle(), candle(close=101)]
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        download_kite_minutes(
            client,
            instruments(),
            "2026-10-05",
            "2026-10-05",
            now="2026-10-05 12:00",
            sleep=lambda _: None,
        )


def test_local_save_zip_and_no_overwrite(tmp_path, monkeypatch):
    result = download_kite_minutes(
        FakeKite(),
        instruments(),
        "2026-10-05",
        "2026-10-05",
        now="2026-10-05 12:00",
        sleep=lambda _: None,
    )
    first = save_kite_download(result, "research", root=tmp_path)
    second = save_kite_download(result, "research", root=tmp_path)
    assert first != second and first.is_dir()
    saved = pd.read_csv(first / "candles_1minute.csv")
    assert saved.volume.iloc[0] == 123
    assert (
        json.loads((first / "download_manifest.json").read_text())["interval"]
        == "minute"
    )
    with zipfile.ZipFile(io.BytesIO(result.to_zip())) as archive:
        assert set(archive.namelist()) == {
            "candles_1minute.csv",
            "instruments.csv",
            "download_manifest.json",
        }
    with pytest.raises(ValueError):
        save_kite_download(result, "../../escape", root=tmp_path)

    def disk_failure(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", disk_failure)
    with pytest.raises(OSError):
        save_kite_download(result, root=tmp_path)
    assert len(list(tmp_path.iterdir())) == 2
    assert not list(tmp_path.glob(".download_*"))


def test_interface_connect_save_disconnect(tmp_path, monkeypatch):
    import frontend.kite_download as menu

    client = FakeKite()
    monkeypatch.setattr(menu, "kite_client", lambda _: client)
    monkeypatch.setattr(menu, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        menu,
        "save_kite_download",
        lambda result, label: save_kite_download(result, label, root=tmp_path),
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Menu").set_value(
        "Download Kite data"
    ).run()
    app.text_input(key="kite_api_key").set_value("test_key")
    app.text_input(key="kite_access_token").set_value("test_token")
    next(b for b in app.button if b.label == "Connect to Kite").click().run()
    assert not app.exception
    assert client.token == "test_token"
    next(b for b in app.button if b.label == "Download and save locally").click().run()
    assert not app.exception and len(app.dataframe) == 1
    manifests = list(tmp_path.glob("*/download_manifest.json"))
    assert len(manifests) == 1
    assert "test_token" not in manifests[0].read_text()
    next(
        b for b in app.button if b.label == "Disconnect and clear credentials"
    ).click().run()
    assert not app.exception
    assert app.text_input(key="kite_api_key").value == ""
    assert app.text_input(key="kite_access_token").value == ""
    assert manifests[0].exists()


def test_login_redirect_flow(monkeypatch):
    import frontend.kite_download as menu

    client = FakeKite()
    monkeypatch.setattr(menu, "kite_client", lambda _: client)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    next(r for r in app.radio if r.label == "Menu").set_value(
        "Download Kite data"
    ).run()
    next(r for r in app.radio if r.label == "Authentication").set_value(
        "Kite login"
    ).run()
    app.text_input(key="kite_api_key").set_value("test_key")
    app.text_input(key="kite_api_secret").set_value("private_secret")
    app.text_input(key="kite_request_token").set_value(
        "http://localhost:8501/?request_token=one_time_request&status=success"
    )
    next(b for b in app.button if b.label == "Connect to Kite").click().run()
    assert not app.exception
    assert client.token == "generated_token"
