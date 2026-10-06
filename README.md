# NIFTY Regime Lab

A modular Python research application implementing the five strategies in the supplied NIFTY top-150 specification. Strategies produce signals through a shared interface; a separate portfolio risk manager sizes and approves orders, and an event-driven backtester executes them. A Streamlit frontend explores portfolio results and downloads CSV/JSON reports.

## Run on this computer

The project-local Python environment is `.venv`. Python does not need to be on your PATH.

```powershell
cd E:\Shishir\demo
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.address 127.0.0.1
```

Open **http://localhost:8501**, select a timeframe, and click **Run backtest**. Alternatively run `powershell -ExecutionPolicy Bypass -File .\start-dashboard.ps1`.

The synthetic demo is clearly labelled, uses fictional symbols, and is seeded for reproducibility. It exercises the software; its results are not evidence of a trading edge. No data subscription or broker credentials are required.

## Install on another computer

Python 3.12 or newer is required. This workspace was tested with Python 3.14.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

With uv: `uv venv --python 3.14 .venv` then `uv pip install --python .venv\Scripts\python.exe -e ".[dev]"`. `uv.lock` captures the verified dependency versions; use `uv sync --extra dev` to reproduce them.

## Command-line backtests

```powershell
# Both daily strategies; all 3/5/10 bps scenarios
.\.venv\Scripts\python.exe -m trading_system.main --demo --output output/demo

# Intraday strategies; demo has 30 complete intraday sessions and daily warmup
.\.venv\Scripts\python.exe -m trading_system.main --demo --mode intraday --output output/intraday

# Combined swing and intraday portfolio, executing on five-minute bars
.\.venv\Scripts\python.exe -m trading_system.main --demo --mode hybrid --output output/hybrid

# Real data and a reserved chronological evaluation period
.\.venv\Scripts\python.exe -m trading_system.main --daily data/private/daily.csv --benchmark data/private/nifty50.csv --universe config/universe.csv --start 2023-01-01 --end 2024-12-31 --output output/research
```

Add `--intraday path.csv --benchmark-intraday path.csv --mode hybrid` to use real five-minute data. The opening strategy needs the intraday benchmark; other intraday strategies can run without it. Missing required benchmark context produces no opening-momentum signals.

## Data contracts

### Download historical one-minute candles with Zerodha Kite

Open **Menu > Download Kite data** in the dashboard. Install the official Python
client in the project environment:

```powershell
uv pip install --python .venv\Scripts\python.exe -e ".[kite-data]"
# On a pip-enabled environment:
.\.venv\Scripts\python.exe -m pip install -e ".[kite-data]"
```

1. Create a Kite Connect app with historical market-data access in the
   [Kite developer console](https://developers.kite.trade/).
2. Enter the API key and a current access token, then click **Connect to Kite**.
   Alternatively select **Kite login**, open Zerodha login, and paste the redirect
   URL (or request token) together with your API secret. Set the registered redirect
   URL to the dashboard address you use, for example `http://localhost:8501/`.
   Use `http://127.0.0.1:8501/` if that is how you open the dashboard. On return,
   the dashboard opens the Kite menu, captures the token, and removes it from
   the browser URL. A new browser tab has a separate session; enter your API key
   and secret again in that tab. Request tokens must be exchanged promptly.
   The API secret comes from the Kite Connect app, not your account password.
3. Select NSE instruments from Kite's instrument master, choose inclusive start
   and end dates, optionally name the dataset, and click **Download and save locally**.
4. The dashboard displays the saved folder, candle preview, coverage manifest,
   and an optional ZIP copy. **Saved local datasets** lists previous downloads.

Every successful download creates a new folder under `data/private/kite/` with
`candles_1minute.csv`, `instruments.csv`, and `download_manifest.json`. Files are
stored on the computer running Streamlit, independently of a browser download.
Existing datasets are preserved. The folder is ignored by git.

After account verification, the API key and access token are saved to
`data/private/kite_credentials.json` on the computer running Streamlit. Opening
the Kite menu restores and verifies this connection, including after browser or
app restarts. The file is the credential source; authenticated clients and tokens
are not retained in dashboard session state. Password inputs are cleared after
saving. API secrets and one-time request tokens are never written to this file,
and credentials are excluded from dataset/report exports and ZIP downloads.
Tokens still expire (normally at 6 AM the next day); reconnect with a fresh token.
If loading instruments fails after login, **Retry saved connection** reuses the
saved access token without exchanging the consumed request token again.

The credential file is unencrypted JSON in the git-ignored `data/private/`
directory. It is written atomically with owner-only file permissions (`0600`) on
POSIX systems; on Windows, access is governed by the directory's ACLs. There is
one saved Kite connection per application installation, shared by its browser
sessions. **Disconnect and clear credentials** deletes this file and clears the
current session's connection data and inputs; other sessions notice the deletion
on their next rerun. Downloaded datasets are preserved.

The downloader uses the official `kiteconnect` client and `historical_data` with
interval `minute`, splitting long ranges into disjoint 30-calendar-day requests.
Requests wait at least one second before each historical API call; transient
failures retry up to three attempts with two- and four-second pauses. HTTP 429
rate-limit errors use longer ten- and twenty-second cooldowns. Retries always
respect a longer configured request delay. Authentication and permission errors
stop immediately. A failed symbol aborts the batch without saving a partial
dataset. Files are written in a temporary
folder and published together after success. Empty request windows are recorded
in the manifest; entirely empty instrument results are rejected.

CSV timestamps retain Kite's **bar-start** convention in **Asia/Kolkata**: 09:15
represents the 09:15-09:16 candle. OHLC and volume come from Kite; missing minutes
and holidays are not filled, and the current unfinished minute is excluded.
Inspect manifest coverage for availability. These one-minute files need separate
aggregation to complete five-minute bar-close sessions plus daily context before
use with the existing backtester. No adjustment or aggregation is applied here.
See [Kite historical data](https://kite.trade/docs/connect/v3/historical/) and
[Kite authentication](https://kite.trade/docs/connect/v3/user/) for API details.

### Download the NIFTY 100 stock list from the command line

`scripts/download_kite_minutes.py` downloads historical one-minute OHLCV using
the same Kite downloader and saved credentials as the dashboard. Connect in the
dashboard once to save a current access token, then run from the project root:

```bash
.venv/bin/python scripts/download_kite_minutes.py --start 2026-09-01 --end 2026-10-05
# Include NIFTY 50 so the downloaded dataset also has a backtest benchmark.
.venv/bin/python scripts/download_kite_minutes.py --start 2026-09-01 --end 2026-10-05 --include-benchmark
# Slow requests further if needed: wait at least two seconds between API calls.
.venv/bin/python scripts/download_kite_minutes.py --start 2026-09-01 --end 2026-10-05 --delay 2
```

On Windows, use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`.
Both dates are inclusive; `--start` is required and `--end` defaults to yesterday
in Asia/Kolkata. Supplying today's date explicitly excludes the unfinished live
minute. By default, the script finds the single `MW-NIFTY-100*.csv` in the project
root (currently `MW-NIFTY-100-05-Oct-2026.csv`), reads its `SYMBOL` column, skips the
`NIFTY 100` summary row, and removes repeated symbols. Use `--stocks-file PATH`
to select a different snapshot or disambiguate multiple matching files. Snapshot
stocks form a fixed user-supplied list, not a historical index-membership universe.

`--delay SECONDS` sets the minimum pause before every historical request,
including between stocks and date batches. It defaults to one second and must
be finite and at least 0.4 seconds. Rate-limit responses trigger ten- and
twenty-second retry cooldowns, or the configured delay if longer. The selected
delay is recorded in the download manifest.

The script resolves exact NSE equity symbols against Kite's instrument list and
stops if any cannot be found. It batches requests, retries transient failures, and
excludes unfinished minutes through the shared downloader. A failed stock aborts
the run without saving a partial dataset. Successful runs create a new folder
under `data/private/kite/` with `candles_1minute.csv`, `instruments.csv`, and
`download_manifest.json`, visible in the dashboard's saved-dataset selector.
`--output DIR` changes the parent directory; `--label NAME` sets a folder label.
Use `--credentials-file PATH` to select another credential file with `api_key`
and `access_token` fields. Tokens are never printed or included in exports.
The CLI is also available as `python -m trading_system.data.kite_stock_downloader`.

### Backtest locally downloaded Kite symbols

In **Backtest dashboard**, choose **Saved Kite downloads** as the data source.
Select one or more saved dataset folders, then select **Downloaded symbols for
backtest** and a **Benchmark index**. Download NIFTY 50 for the same dates as the
stocks; a separate benchmark download can be combined with stock downloads in
this selector. Edit sectors under **Selected universe / sectors** if needed.
The selected stocks form a fixed user-defined research universe.

The dashboard builds daily OHLCV and, for intraday/hybrid mode, five-minute OHLCV
with bar-close timestamps from Kite's one-minute bar-start candles. It keeps only
sessions with all 375 regular-market minutes from 09:15 through 15:29 and only
dates shared by every selected stock and the benchmark. Incomplete/current sessions
are excluded, with coverage shown in the sidebar. Missing prices are not filled.
Overlapping identical candles are deduplicated; conflicting overlaps are rejected.
Provider prices are used without applying corporate-action adjustments.

Choose the research period and click **Run backtest**. Earlier downloaded sessions
remain available for warmup. Most strategies need at least 200 daily sessions;
short downloads may run with no trades. The last completed run identifies its
selected stocks and benchmark so changed controls cannot be mistaken for the
previous results. No Kite login or network access is needed to backtest saved files.

### Download daily history with yfinance

The optional downloader fetches daily stock OHLCV plus NIFTY 50 (`^NSEI`) and produces CSVs directly usable by this application's CLI or upload dashboard.

In the existing uv-created environment, install with `uv pip install --python .venv\Scripts\python.exe -e ".[market-data]"`; the optional dependency is already installed in this workspace. On a standard pip-enabled environment use the command below.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market-data]"

# Download a sample of real NSE tickers; replace this CSV with your full universe.
.\.venv\Scripts\python.exe scripts/download_historical.py --universe config/universe_nse_example.csv --start 2012-01-01 --output data/private/yfinance

# Or provide individual symbols. Plain tickers receive the .NS suffix.
.\.venv\Scripts\python.exe scripts/download_historical.py --symbols RELIANCE TCS INFY --start 2020-01-01 --end 2025-01-01 --output data/private/custom

# Test the downloaded data; this includes warmup history from the CSV.
.\.venv\Scripts\python.exe -m trading_system.main --daily data/private/yfinance/daily.csv --benchmark data/private/yfinance/benchmark.csv --universe data/private/yfinance/universe.csv --start 2023-01-01 --output output/yfinance
```

The same downloader is available as `python -m trading_system.data.yfinance_downloader` or the installed `nifty-download` command. `--start` is inclusive; `--end` is exclusive and defaults to today's date in Asia/Kolkata, excluding today's unfinished daily bar. The script writes `daily.csv`, `benchmark.csv`, `universe.csv`, and `download_manifest.json`. Downloaded files default to the ignored `data/private/` folder. Use `--overwrite` to replace an existing dataset. CLI symbol lists get `Unknown` sector metadata; edit the exported universe to set sectors before applying sector risk limits. The example NSE universe lists only five sample stocks and makes no claim about historical index membership. The original `config/universe.csv` remains the fictional demo universe and is rejected by the downloader.

Universe input may include `yahoo_symbol` overrides while retaining your internal `symbol`, sector and membership dates. Repeated historical membership intervals download the ticker once and remain in the exported universe. Dates are never inferred from the current index. Downloads are sequential with configurable `--retries`, `--delay`, and `--timeout`. By default a failed stock or benchmark aborts without saving a dataset. `--allow-partial` explicitly permits failed stocks to be excluded, recording their errors and the resulting coverage in the manifest; benchmark failure remains fatal. An incomplete universe changes breadth and relative-strength ranks, so inspect the manifest before comparing runs.

The adapter requests raw OHLC and adjusted close, then applies `Adj Close / Close` to **all** OHLC columns. Exported `adjusted_close` equals `close`, satisfying the engine's consistent-price-basis requirement. Volume remains Yahoo's reported volume; turnover is raw reported close × reported volume, an approximation rather than official NSE traded value. Empty, partly missing, non-finite, duplicate or invalid bars are rejected; entirely blank provider rows are removed. Yahoo can revise corporate-action adjustments and historical data. Save the manifest and CSVs with each research run. See the official [yfinance download API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) for date and adjustment semantics and the [project's usage notice](https://github.com/ranaroussi/yfinance) for Yahoo data terms. This script downloads daily history; it does not produce the complete five-minute sessions required by the intraday engine.

Daily and five-minute CSVs contain `timestamp,symbol,open,high,low,close,volume`. Optional daily fields include `adjusted_close` and `turnover`. Prices and volume must use a consistent corporate-action-adjusted basis. If `adjusted_close` differs from `close`, ingestion rejects the mixed basis rather than silently combining incompatible prices. If adjustment flags are present, clear them after upstream adjustment. Missing adjusted_close means the caller asserts the supplied OHLC is already suitable for research.

Naive timestamps are interpreted in **Asia/Kolkata**. Daily timestamps normalize to 15:30 IST. Five-minute timestamps denote **bar close**: 09:20 is the 09:15–09:20 bar. The opening range includes closes through 09:30 (three bars), and breakout signals begin at 09:35. Intraday mode requires every supplied symbol/session to contain all 75 close timestamps from 09:20 through 15:30. Missing bars are rejected; the system does not invent a 15:10 exit price. Inputs must not include auction or out-of-session bars. Users supply an exchange-calendar-cleaned historical dataset; business days in the demo do not model NSE holidays.

Universe CSV requires `symbol,sector`; supported columns are `index_group,lot_size,is_fno,active_from,active_to`. Dates are inclusive. Multiple non-overlapping membership intervals per symbol are supported. Membership filters and cross-sectional ranks use only the eligible universe at that date. Replace `config/universe.csv` with your historical top-150 universe; the sample eight-symbol universe is fictional, not a current constituent list. At least 200 daily warmup sessions are needed for trend eligibility. Missing/invalid indicator history suppresses signals. Intraday features join only daily sessions strictly earlier than the signal timestamp; daily context over seven calendar days old is rejected.

Daily liquidity defaults to price ≥ ₹50 and 20-session average traded value ≥ ₹20 crore. Daily volume reference for breakouts is shifted to exclude the breakout session. The relative-volume baseline is the previous 20 sessions' average cumulative volume at the same clock time, requiring five historical sessions. Sector residuals use contemporaneous eligible peers' session returns.

## Architecture

```text
config/                 YAML parameters and historical membership CSV
trading_system/
  models.py             Signal, Order, Position, Trade, Regime
  data/                 Validation, CSV ingestion, causal features, synthetic demo
  indicators/           SMA, EMA, Wilder ATR/ADX, RSI, session VWAP, breadth/ranks
  regime/               Deterministic benchmark + breadth classifier
  strategies/           Common base, registry, five independent strategy classes
  portfolio/            Position sizing and portfolio risk controls
  backtest/             Event engine, fills, fees, metrics, walk-forward/robustness
  reports/              CSV/JSON exports and attribution
  execution/            Broker interface, paper recorder, disabled live adapter
frontend/app.py         Interactive results and validation dashboard
tests/                  Indicators, causal features, risk/fills, engine and UI checks
examples/sample_output/ Reproducible synthetic daily results at 3/5/10 bps
```

All five strategies can be enabled independently in `config/settings.yaml`:

- **MomentumPullback:** bullish swing, ranked relative strength, controlled pullback, reversal confirmation, technical/ATR stop, two-close EMA exit and trailing stop.
- **VolatilityBreakout:** bullish or directional flat swing, pre-breakout volatility/ATR compression, volume-confirmed shifted-high breakout, next-session stop order, trailing/10-day-low exits.
- **WeaknessBreakdown:** bearish intraday, lagged daily weakness and support, VWAP/relative-volume confirmation, 2.5R target or invalidation.
- **VWAPReversion:** flat/non-directional intraday, neutral breadth, VWAP deviation, RSI and sector residual with reversal confirmation, fixed signal-time VWAP target.
- **OpeningMomentum:** directional bullish/bearish intraday, benchmark/VWAP/breadth confirmation, completed opening range, midpoint stop and 2R target.

## Add a strategy

Create one class inheriting `BaseStrategy`, give it a unique `name` and `timeframe` (`daily` or `intraday`), and implement `generate_signals(market_data, benchmark_data, regime, timestamp)`. Optionally override `exit_reason(position,row)` for close-confirmed exits. Use `make_signal` for shared validation and metadata. Feature rows contain information available by the signal close; strategies should not obtain future raw data or submit broker orders.

```python
from trading_system.strategies import register_strategy
from trading_system.strategies.base import BaseStrategy

@register_strategy
class MyStrategy(BaseStrategy):
    name = "my_strategy"
    timeframe = "daily"

    def generate_signals(self, market_data, benchmark_data, regime, timestamp):
        return []  # Return validated Signal instances here.
```

Import the module from `trading_system/strategies/__init__.py` so it registers at startup, then add `strategies.my_strategy` parameters to YAML. The engine discovers enabled classes through the registry. Add feature computation in `data/features.py` if needed and prefix-invariance tests for any new rolling logic.

## Execution and risk conventions

Signals are generated at bar close; market orders fill at the next eligible bar open. Daily-only entries are timestamped with that next daily bar's close timestamp; the price is its open. Use hybrid mode for actual intraday ordering of swing fills. In hybrid mode, swing orders fill at the next session's first five-minute bar open; current-day daily bars never inform intraday signals.

Orders expire after one eligible bar. Stop-entry prices account for gaps; limits require a permitted trading range. Stops and targets are checked against OHLC; if both occur in one bar, the stop wins. Stop/limit entry bars do not receive an optimistic same-bar target. Price-path ambiguity may make these fills pessimistic; tick-level execution is outside scope. Trailing stops are updated only after the bar closes and become active on later bars. Close-confirmed discretionary exits fill on the next bar open. Initial targets stay anchored to signal prices; entries are rejected if a gap has already passed the target. Intraday positions close at the configured 15:10 bar close, and entries on/after that cutoff are blocked. Positions remaining at the end of a run are liquidated at their last actual bar close and charged fees.

Risk controls cover 0.5% equity risk per trade (including entry fees), 30% stock allocation, three positions, two per sector, duplicate symbols, and 100% gross exposure. Shorts reserve full notional capacity. The loss locks compare marked equity with start-of-day/week equity and remain latched until the next day/week even if equity recovers. Existing positions follow their normal exits. Gap/slippage losses can exceed the initial stop-based budget. Cash shorts are restricted to intraday.

## Costs and reports

The separate model charges brokerage, STT, exchange/SEBI fees, GST, stamp duty, and adverse spread/slippage on both legs. Configured delivery and intraday rates differ. Fee defaults are editable **constant research assumptions**, not a complete effective-dated historical tariff. Historical fees, settlement-specific costs (including DP charges), market impact, and broker-specific rounding need a dated cost adapter for production-grade studies. NSE references: [STT, SEBI fees and GST](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies), [stamp duty](https://www.nseindia.com/static/invest/first-time-investor-stamp-duty-charges-taxes). No returns are promised.

Each report includes 3/5/10 bps comparison, equity/drawdown, daily turnover, monthly/yearly returns, rolling Sharpe/drawdown, capital utilization, strategy overlap and sector exposure, trade ledger, top/bottom contributors, and strategy breakdowns by year, regime, symbol, sector, direction, volatility and liquidity quartile. A data-fingerprint manifest and saved settings support reproduction. Friction is embedded in fill prices; `costs` reports explicit transaction fees separately. Average R uses net P&L divided by initial filled-price risk. Portfolio ratios use end-of-session marks; exposure percentage is the fraction of event observations with exposure. Strategy grouped reports summarize realized trade P&L rather than claiming separately funded strategy CAGR.

## Chronological validation

`backtest.walk_forward.windows` generates rolling train/validation periods (default five years plus one year). `evaluate_walk_forward` freezes settings for each validation run, initializes indicators with prior history, and accepts an optional selector that receives only training data. Each fold starts with fresh capital and no carried training positions. `robustness` evaluates at most five neighboring values, without selecting an optimum. Both are available in the dashboard. Reserve development, validation, out-of-sample, and final untouched periods explicitly with CLI date filters. Keep the final test period outside any parameter-selection workflow. No random splitting, unrestricted optimizer, or machine learning is included.

## Current scope

The research/backtest system and frontend are implemented, with optional yfinance daily-data and Zerodha Kite historical one-minute OHLCV downloaders. Kite downloads save complete datasets to local storage. Live brokerage, streaming market data, durable paper-trading state/recovery, effective-dated tariffs, optional spread/turnover filters, correlation rejection, and Monte Carlo trade-sequence analysis remain future modules. `PaperBroker` is an order recorder for adapter development. `LiveBroker` deliberately raises until a validated adapter exists. This project does not publish the repository remotely.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check trading_system frontend tests scripts
```


### Troubleshooting Kite login

- **Token exchange / checksum error:** use the API key and API secret from the
  same active Kite Connect app. After correcting them, start a fresh Zerodha login.
- **Invalid, expired, or used request token:** request tokens are short-lived and
  single-use. Obtain a new one. You can paste a full redirect URL, its query
  string, or just the token. Do not paste an access token into the request-token field.
- **Login succeeded, instruments could not be loaded:** click **Connect to Kite**
  again. The dashboard reuses the exchanged access token instead of exchanging
  the one-time request token again.
- **Redirect does not reach the app:** check the dashboard is running and the
  redirect URL in the developer console matches the hostname, port, and path.
  Login failures show the operation, error type, and HTTP status without exposing
  credentials. Share that displayed error if you need further diagnosis.
