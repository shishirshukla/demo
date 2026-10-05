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

The research/backtest system and frontend are implemented. Live brokerage, streaming market data, durable paper-trading state/recovery, effective-dated tariffs, optional spread/turnover filters, correlation rejection, and Monte Carlo trade-sequence analysis remain future modules. `PaperBroker` is an order recorder for adapter development. `LiveBroker` deliberately raises until a validated adapter exists. This project does not automatically download proprietary market data or publish the repository remotely.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check trading_system frontend tests scripts
```

