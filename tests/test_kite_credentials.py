import json
import os
import stat
from pathlib import Path

import pytest

from trading_system.data.kite_credentials import (
    clear_kite_credentials,
    load_kite_credentials,
    save_kite_credentials,
)


def test_credentials_round_trip_replacement_and_deletion(tmp_path):
    path = tmp_path / "private" / "kite_credentials.json"
    assert load_kite_credentials(path=path) is None
    save_kite_credentials(" test_key ", " private_token ", path=path)
    credentials = load_kite_credentials(path=path)
    assert credentials.api_key == "test_key"
    assert credentials.access_token == "private_token"
    assert "private_token" not in repr(credentials)
    assert "test_key" not in repr(credentials)
    assert json.loads(path.read_text()) == {
        "api_key": "test_key",
        "access_token": "private_token",
    }
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    save_kite_credentials("another_key", "another_token", path=path)
    assert load_kite_credentials(path=path).access_token == "another_token"
    clear_kite_credentials(path=path)
    clear_kite_credentials(path=path)
    assert load_kite_credentials(path=path) is None


@pytest.mark.parametrize(
    "contents",
    [
        "private_token: broken JSON",
        "null",
        "[]",
        '{"api_key": "test_key"}',
        '{"api_key": "test_key", "access_token": ""}',
        '{"api_key": 123, "access_token": "private_token"}',
        '{"api_key": "test_key", "access_token": "private token"}',
    ],
)
def test_invalid_credential_file_errors_are_redacted(tmp_path, contents):
    path = tmp_path / "kite_credentials.json"
    path.write_text(contents)
    with pytest.raises(ValueError, match="Invalid Kite credential file") as error:
        load_kite_credentials(path=path)
    assert "private_token" not in str(error.value)
    assert "test_key" not in str(error.value)


def test_failed_save_preserves_existing_credentials_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "kite_credentials.json"
    save_kite_credentials("test_key", "original_token", path=path)

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        save_kite_credentials("test_key", "replacement_token", path=path)
    assert load_kite_credentials(path=path).access_token == "original_token"
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_credentials_do_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "kite_credentials.json"
    save_kite_credentials("test_key", "original_token", path=path)
    with pytest.raises(ValueError):
        save_kite_credentials("test_key", "", path=path)
    assert load_kite_credentials(path=path).access_token == "original_token"
