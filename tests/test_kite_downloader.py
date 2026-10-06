import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from trading_system.data.kite_credentials import save_kite_credentials
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


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    import trading_system.data.kite_credentials as credentials

    path = tmp_path / "kite_credentials.json"
    monkeypatch.setattr(credentials, "CREDENTIALS_PATH", path)
    return path


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
        self.access_token = token

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
    assert len(attempts) == 3 and delays == [1.0, 2, 4]

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
    assert delays == [1.0]


def test_configured_delay_applies_across_stocks_and_date_batches():
    client = FakeKite()
    selected = pd.DataFrame(
        [INSTRUMENT, {**INSTRUMENT, "tradingsymbol": "TCS", "instrument_token": 123}]
    )
    delays = []
    result = download_kite_minutes(
        client,
        selected,
        "2026-08-01",
        "2026-09-05",
        now="2026-10-05 12:00",
        request_delay=2.5,
        sleep=delays.append,
    )
    assert len(client.calls) == 4 and delays == [2.5] * 4
    assert result.manifest["request_delay_seconds"] == 2.5


@pytest.mark.parametrize(
    "code, delay, expected", [(429, 1.0, [1.0, 10, 20]), (500, 5.0, [5.0, 5.0, 5.0])]
)
def test_retry_cooldown_respects_rate_limits_and_configured_delay(
    code, delay, expected
):
    from kiteconnect.exceptions import GeneralException

    client = FakeKite()
    original = client.historical_data
    attempts, waits = [], []

    def fail_twice(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise GeneralException("private response", code=code)
        return original(*args, **kwargs)

    client.historical_data = fail_twice
    download_kite_minutes(
        client,
        instruments(),
        "2026-10-05",
        "2026-10-05",
        now="2026-10-05 12:00",
        request_delay=delay,
        sleep=waits.append,
    )
    assert len(attempts) == 3 and waits == expected


def test_exhausted_rate_limit_is_actionable_and_redacted():
    from kiteconnect.exceptions import GeneralException

    client = FakeKite()

    def rate_limited(*args, **kwargs):
        raise GeneralException("private_token rate limited", code=429)

    client.historical_data = rate_limited
    delays = []
    with pytest.raises(RuntimeError, match="Increase the request delay") as error:
        download_kite_minutes(
            client,
            instruments(),
            "2026-10-05",
            "2026-10-05",
            now="2026-10-05 12:00",
            sleep=delays.append,
        )
    assert delays == [1.0, 10, 20]
    assert "private_token" not in str(error.value)


@pytest.mark.parametrize("delay", [0, -1, 0.39, float("nan"), float("inf")])
def test_invalid_delay_is_rejected_before_requests(delay):
    client = FakeKite()
    delays = []
    with pytest.raises(ValueError, match="at least 0.4 seconds"):
        download_kite_minutes(
            client,
            instruments(),
            "2026-10-05",
            "2026-10-05",
            now="2026-10-05 12:00",
            request_delay=delay,
            sleep=delays.append,
        )
    assert not delays and not client.calls


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


def test_interface_connect_save_disconnect(tmp_path, monkeypatch, isolated_credentials):
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
    assert json.loads(isolated_credentials.read_text()) == {
        "api_key": "test_key",
        "access_token": "test_token",
    }
    assert "kite_client" not in app.session_state
    assert "kite_pending_client" not in app.session_state
    assert app.text_input(key="kite_access_token").value == ""
    next(b for b in app.button if b.label == "Download and save locally").click().run()
    assert not app.exception and len(app.dataframe) == 1
    manifests = list(tmp_path.glob("*/download_manifest.json"))
    assert len(manifests) == 1
    assert "test_token" not in manifests[0].read_text()
    # A fresh browser session restores the saved token without password inputs.
    reopened = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    reopened.radio(key="workspace_menu").set_value("Download Kite data").run()
    assert not reopened.exception and not reopened.error
    assert any("Connected as TEST123" in message.value for message in reopened.success)
    assert reopened.text_input(key="kite_api_key").value == ""
    assert reopened.text_input(key="kite_access_token").value == ""
    next(
        b for b in app.button if b.label == "Disconnect and clear credentials"
    ).click().run()
    assert not app.exception
    assert app.text_input(key="kite_api_key").value == ""
    assert app.text_input(key="kite_access_token").value == ""
    assert manifests[0].exists()
    assert not isolated_credentials.exists()
    reopened.run()
    assert not reopened.exception and not reopened.success
    assert all(b.label != "Download and save locally" for b in reopened.button)


def test_login_redirect_flow(monkeypatch, isolated_credentials):
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
    assert json.loads(isolated_credentials.read_text()) == {
        "api_key": "test_key",
        "access_token": "generated_token",
    }
    assert app.text_input(key="kite_api_secret").value == ""
    assert app.text_input(key="kite_request_token").value == ""
    assert "kite_client" not in app.session_state


@pytest.mark.parametrize(
    "value",
    [
        "one_time_request",
        "?request_token=one_time_request&status=success",
        "request_token=one_time_request&status=success",
        "http://localhost:8501/?request_token=one_time_request&status=success",
    ],
)
def test_request_token_formats(value):
    from frontend.kite_download import extract_request_token

    assert extract_request_token(value) == "one_time_request"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://localhost:8501/",
        "?request_token=first&request_token=second",
        "?request_token=abc&status=failed",
    ],
)
def test_bad_login_returns_rejected(value):
    from frontend.kite_download import extract_request_token

    with pytest.raises(ValueError):
        extract_request_token(value)


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Invalid checksum private_secret", "API secret/checksum"),
        ("Invalid api_key test_key", "API key"),
        ("Invalid request_token one_time_request", "expired, or already used"),
    ],
)
def test_token_exchange_errors_are_actionable_and_redacted(message, expected):
    from kiteconnect.exceptions import TokenException

    from frontend.kite_download import exchange_login

    client = FakeKite()

    def fail(*args, **kwargs):
        raise TokenException(message, code=403)

    client.generate_session = fail
    with pytest.raises(RuntimeError) as error:
        exchange_login(client, "one_time_request", "private_secret")
    text = str(error.value)
    assert expected in text and "HTTP 403" in text
    assert (
        "private_secret" not in text
        and "one_time_request" not in text
        and "test_key" not in text
    )


def test_browser_callback_opens_kite_menu_and_clears_token_url():
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    )
    app.query_params.update(
        {
            "request_token": "one_time_request",
            "status": "success",
            "action": "login",
            "keep": "yes",
        }
    )
    app.run()
    assert not app.exception
    assert app.title[0].value == "Download Kite data"
    assert app.radio(key="kite_auth_method").value == "Kite login"
    assert app.text_input(key="kite_request_token").value == "one_time_request"
    assert "request_token" not in app.query_params
    assert app.query_params["keep"] == "yes"


def test_instruments_retry_does_not_exchange_consumed_token(
    monkeypatch, isolated_credentials
):
    from kiteconnect.exceptions import NetworkException

    import frontend.kite_download as menu

    client = FakeKite()
    exchanges = []
    original_exchange = client.generate_session

    def exchange(*args, **kwargs):
        exchanges.append(1)
        return original_exchange(*args, **kwargs)

    client.generate_session = exchange
    loads = []

    def load(exchange):
        loads.append(1)
        if len(loads) == 1:
            raise NetworkException("network down")
        return [INSTRUMENT]

    client.instruments = load
    monkeypatch.setattr(menu, "kite_client", lambda _: client)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    )
    app.query_params.update({"request_token": "one_time_request", "status": "success"})
    app.run()
    app.text_input(key="kite_api_key").set_value("test_key")
    app.text_input(key="kite_api_secret").set_value("private_secret")
    next(b for b in app.button if b.label == "Connect to Kite").click().run()
    assert not app.exception and "Login succeeded" in app.error[0].value
    assert isolated_credentials.exists()
    assert app.text_input(key="kite_request_token").value == ""
    next(b for b in app.button if b.label == "Retry saved connection").click().run()
    assert not app.exception and not app.error
    assert exchanges == [1] and loads == [1, 1]


def test_saved_token_expiry_is_redacted_and_can_be_cleared(
    monkeypatch, isolated_credentials
):
    from kiteconnect.exceptions import TokenException

    import frontend.kite_download as menu

    client = FakeKite()

    def expired():
        raise TokenException("expired private_token test_key")

    client.profile = expired
    monkeypatch.setattr(menu, "kite_client", lambda _: client)
    save_kite_credentials("test_key", "private_token")
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    app.radio(key="workspace_menu").set_value("Download Kite data").run()
    assert not app.exception
    assert "session expired" in app.error[0].value
    assert "private_token" not in app.error[0].value
    assert "test_key" not in app.error[0].value
    assert all(b.label != "Download and save locally" for b in app.button)
    next(
        b for b in app.button if b.label == "Disconnect and clear credentials"
    ).click().run()
    assert not app.exception and not app.error
    assert not isolated_credentials.exists()


def test_credential_write_failure_does_not_connect(monkeypatch, isolated_credentials):
    import frontend.kite_download as menu

    monkeypatch.setattr(menu, "kite_client", lambda _: FakeKite())

    def fail(*args, **kwargs):
        raise OSError("private_token disk full")

    monkeypatch.setattr(menu, "save_kite_credentials", fail)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    app.radio(key="workspace_menu").set_value("Download Kite data").run()
    app.text_input(key="kite_api_key").set_value("test_key")
    app.text_input(key="kite_access_token").set_value("private_token")
    next(b for b in app.button if b.label == "Connect to Kite").click().run()
    assert not app.exception and not app.success
    assert "Could not save Kite credentials" in app.error[0].value
    assert "private_token" not in app.error[0].value
    assert not isolated_credentials.exists()
    assert "kite_client" not in app.session_state


def test_failed_credential_deletion_keeps_connection(monkeypatch, isolated_credentials):
    import frontend.kite_download as menu

    monkeypatch.setattr(menu, "kite_client", lambda _: FakeKite())
    save_kite_credentials("test_key", "private_token")

    def fail():
        raise PermissionError("private_token permission denied")

    monkeypatch.setattr(menu, "clear_kite_credentials", fail)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    app.radio(key="workspace_menu").set_value("Download Kite data").run()
    next(
        b for b in app.button if b.label == "Disconnect and clear credentials"
    ).click().run()
    assert not app.exception
    assert "Could not delete the saved credentials" in app.error[0].value
    assert "private_token" not in app.error[0].value
    assert isolated_credentials.exists()
    assert any("Connected as TEST123" in message.value for message in app.success)
