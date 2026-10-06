import json
from functools import partial

import pandas as pd
import pytest

from trading_system.data import kite_stock_downloader as cli
from trading_system.data.kite_credentials import save_kite_credentials
from trading_system.data.kite_downloader import download_kite_minutes


def instrument(symbol, token, *, segment="NSE", kind="EQ", exchange="NSE"):
    return {
        "tradingsymbol": symbol,
        "instrument_token": token,
        "exchange": exchange,
        "instrument_type": kind,
        "segment": segment,
    }


def master():
    return pd.DataFrame(
        [
            instrument("TCS", 2),
            instrument("NIFTY 100", 4, segment="INDICES"),
            instrument("RELIANCE", 1),
            instrument("NIFTY 50", 3, segment="INDICES"),
        ]
    )


class FakeKite:
    def __init__(self):
        self.calls = []
        self.profile_calls = 0
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token

    def profile(self):
        self.profile_calls += 1
        return {"user_id": "TEST123"}

    def instruments(self, exchange):
        assert exchange == "NSE"
        return master().to_dict("records")

    def historical_data(self, token, first, last, interval, **kwargs):
        self.calls.append((token, first, last, interval, kwargs))
        timestamp = pd.Timestamp(first).normalize() + pd.Timedelta(hours=9, minutes=15)
        return [
            {
                "date": timestamp.tz_localize("Asia/Kolkata").to_pydatetime(),
                "open": 100,
                "high": 105,
                "low": 98,
                "close": 102,
                "volume": 123,
            }
        ]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    import trading_system.data.kite_credentials as credentials

    stock_file = tmp_path / "MW-NIFTY-100-snapshot.csv"
    stock_file.write_text(
        'SYMBOL,LTP\n"NIFTY 100","23,645.30"\nRELIANCE,1186.4\nTCS,2114.4\n',
        encoding="utf-8-sig",
    )
    credential_file = tmp_path / "kite_credentials.json"
    save_kite_credentials("test_key", "private_token", path=credential_file)
    monkeypatch.setattr(credentials, "CREDENTIALS_PATH", credential_file)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    client = FakeKite()

    def make_client(api_key):
        assert api_key == "test_key"
        return client

    monkeypatch.setattr(cli, "kite_client", make_client)
    monkeypatch.setattr(
        cli,
        "download_kite_minutes",
        partial(download_kite_minutes, sleep=lambda _: None),
    )
    return stock_file, credential_file, client, tmp_path / "downloads"


def test_csv_bom_summary_duplicates_and_symbol_punctuation(tmp_path):
    path = tmp_path / "stocks.csv"
    path.write_text(
        " symbol ,LTP\nNIFTY 100,1\n reliance ,2\nM&M,3\nRELIANCE,4\nBAJAJ-AUTO,5\n",
        encoding="utf-8-sig",
    )
    assert cli.read_stock_symbols(path) == ["RELIANCE", "M&M", "BAJAJ-AUTO"]


@pytest.mark.parametrize(
    "contents, error",
    [
        ("NAME\nTCS\n", "SYMBOL column"),
        ('SYMBOL,LTP\n"",1\n', "empty SYMBOL"),
        ("SYMBOL\nNIFTY 100\n", "no stocks"),
    ],
)
def test_bad_stock_list_is_rejected(tmp_path, contents, error):
    path = tmp_path / "stocks.csv"
    path.write_text(contents)
    with pytest.raises(ValueError, match=error):
        cli.read_stock_symbols(path)


def test_discovery_requires_a_single_matching_file(tmp_path):
    with pytest.raises(ValueError, match="No MW-NIFTY-100"):
        cli.find_stock_file(tmp_path)
    first = tmp_path / "MW-NIFTY-100-first.csv"
    first.write_text("SYMBOL\nTCS\n")
    assert cli.find_stock_file(tmp_path) == first
    (tmp_path / "MW-NIFTY-100-second.csv").write_text("SYMBOL\nTCS\n")
    with pytest.raises(ValueError, match="Multiple MW-NIFTY-100"):
        cli.find_stock_file(tmp_path)


def test_resolution_preserves_stock_order_and_explicit_benchmark():
    selected = cli.select_instruments(["RELIANCE", "TCS"], master())
    assert selected.instrument_token.tolist() == [1, 2]
    selected = cli.select_instruments(
        ["RELIANCE", "TCS"], master(), include_benchmark=True
    )
    assert selected.instrument_token.tolist() == [1, 2, 3]


@pytest.mark.parametrize(
    "changes", [{"exchange": "BSE"}, {"segment": "INDICES"}, {"instrument_type": "FUT"}]
)
def test_resolution_requires_nse_equities(changes):
    instruments = master()
    for column, value in changes.items():
        instruments.loc[instruments.tradingsymbol == "TCS", column] = value
    with pytest.raises(ValueError, match="TCS.*No candles requested"):
        cli.select_instruments(["TCS"], instruments)


def test_ambiguous_equity_symbol_is_rejected():
    instruments = pd.concat(
        [master(), pd.DataFrame([instrument("TCS", 99)])], ignore_index=True
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        cli.select_instruments(["TCS"], instruments)


@pytest.mark.parametrize("benchmark", [False, True])
def test_cli_downloads_and_saves_dashboard_compatible_dataset(
    environment, capsys, benchmark
):
    stock_file, _, client, output = environment
    args = ["--start", "2026-10-05", "--output", str(output)]
    if benchmark:
        args.append("--include-benchmark")
    cli.main(args, now="2026-10-06 10:00")
    folders = list(output.iterdir())
    assert len(folders) == 1
    folder = folders[0]
    assert set(path.name for path in folder.iterdir()) == {
        "candles_1minute.csv",
        "instruments.csv",
        "download_manifest.json",
    }
    assert client.access_token == "private_token"
    assert [call[0] for call in client.calls] == ([1, 2, 3] if benchmark else [1, 2])
    assert all(call[3] == "minute" for call in client.calls)
    candles = pd.read_csv(folder / "candles_1minute.csv")
    assert candles.symbol.tolist() == (
        ["RELIANCE", "TCS", "NIFTY 50"] if benchmark else ["RELIANCE", "TCS"]
    )
    manifest = json.loads((folder / "download_manifest.json").read_text())
    assert manifest["requested_start"] == "2026-10-05"
    assert manifest["requested_end_inclusive"] == "2026-10-05"
    assert manifest["stock_list_file"] == stock_file.name
    assert manifest["requested_stock_symbols"] == ["RELIANCE", "TCS"]
    assert manifest["benchmark_symbol"] == ("NIFTY 50" if benchmark else None)
    assert manifest["request_delay_seconds"] == 1.0
    printed = capsys.readouterr()
    assert "Saved" in printed.out
    assert "private_token" not in printed.out + printed.err
    assert "test_key" not in printed.out + printed.err
    assert "private_token" not in (folder / "download_manifest.json").read_text()


def test_cli_explicit_stock_and_credential_paths_override_defaults(
    environment, tmp_path
):
    stock_file, credential_file, client, output = environment
    (tmp_path / "MW-NIFTY-100-other.csv").write_text("SYMBOL\nUNKNOWN\n")
    cli.main(
        [
            "--stocks-file",
            str(stock_file),
            "--credentials-file",
            str(credential_file),
            "--start",
            "2026-10-05",
            "--end",
            "2026-10-05",
            "--output",
            str(output),
            "--label",
            "research",
        ],
        now="2026-10-06 10:00",
    )
    assert len(client.calls) == 2
    assert next(output.iterdir()).name.endswith("_research")


@pytest.mark.parametrize(
    "args, code",
    [
        (["--start", "2026-10-06", "--end", "2026-10-05"], 1),
        (["--start", "2026-10-05", "--end", "2026-10-07"], 1),
        (["--start", "2026-10-05", "--label", "../escape"], 1),
        (["--start", "2026-02-30"], 2),
        (["--start", "20261005"], 2),
        (["--start", "2026-10-05", "--delay", "0"], 1),
        (["--start", "2026-10-05", "--delay", "-1"], 1),
        (["--start", "2026-10-05", "--delay", "0.39"], 1),
        (["--start", "2026-10-05", "--delay", "nan"], 1),
        (["--start", "2026-10-05", "--delay", "inf"], 1),
    ],
)
def test_bad_cli_arguments_fail_before_contacting_kite(environment, args, code):
    _, _, client, _ = environment
    with pytest.raises(SystemExit) as error:
        cli.main(args, now="2026-10-06 10:00")
    assert error.value.code == code
    assert client.profile_calls == 0 and not client.calls


def test_cli_passes_configured_delay_to_all_requests(environment, monkeypatch):
    _, _, client, output = environment
    delays = []
    monkeypatch.setattr(
        cli,
        "download_kite_minutes",
        partial(download_kite_minutes, sleep=delays.append),
    )
    cli.main(
        ["--start", "2026-10-05", "--output", str(output), "--delay", "2"],
        now="2026-10-06 10:00",
    )
    assert len(client.calls) == 2 and delays == [2.0, 2.0]
    manifest = json.loads(next(output.glob("*/download_manifest.json")).read_text())
    assert manifest["request_delay_seconds"] == 2.0


def test_missing_credentials_give_login_instructions(environment, capsys):
    _, credential_file, client, _ = environment
    credential_file.unlink()
    with pytest.raises(SystemExit) as error:
        cli.main(["--start", "2026-10-05"], now="2026-10-06 10:00")
    assert error.value.code == 1
    assert "No saved Kite credentials" in capsys.readouterr().err
    assert client.profile_calls == 0 and not client.calls


def test_unresolved_stock_aborts_before_requesting_candles(environment, capsys):
    stock_file, _, client, output = environment
    stock_file.write_text("SYMBOL\nRELIANCE\nUNKNOWN\n")
    with pytest.raises(SystemExit) as error:
        cli.main(
            ["--start", "2026-10-05", "--output", str(output)], now="2026-10-06 10:00"
        )
    assert error.value.code == 1
    assert "UNKNOWN" in capsys.readouterr().err
    assert not client.calls and not output.exists()


def test_expired_tokens_are_redacted_and_do_not_start_a_download(environment, capsys):
    from kiteconnect.exceptions import TokenException

    _, _, client, output = environment

    def expired():
        raise TokenException("expired private_token test_key")

    client.profile = expired
    with pytest.raises(SystemExit) as error:
        cli.main(
            ["--start", "2026-10-05", "--output", str(output)], now="2026-10-06 10:00"
        )
    assert error.value.code == 1
    printed = capsys.readouterr()
    assert "expired or invalid" in printed.err
    assert "private_token" not in printed.out + printed.err
    assert "test_key" not in printed.out + printed.err
    assert not client.calls and not output.exists()


def test_failed_stock_does_not_save_partial_results(environment, capsys):
    from kiteconnect.exceptions import PermissionException

    _, _, client, output = environment
    original = client.historical_data

    def denied(token, *args, **kwargs):
        if token == 2:
            raise PermissionException("private_token test_key denied")
        return original(token, *args, **kwargs)

    client.historical_data = denied
    with pytest.raises(SystemExit) as error:
        cli.main(
            ["--start", "2026-10-05", "--output", str(output)], now="2026-10-06 10:00"
        )
    assert error.value.code == 1
    printed = capsys.readouterr()
    assert "No dataset saved" in printed.err
    assert "private_token" not in printed.out + printed.err
    assert "test_key" not in printed.out + printed.err
    assert len(client.calls) == 1 and not output.exists()
