# AGENTS.md

## Project overview

This repository is the NIFTY Regime Lab: a modular Python research and backtesting application for NSE/NIFTY regime strategies. The codebase implements five strategy families behind a shared signal interface, a portfolio risk manager for position sizing and order approval, and a backtester that consumes validated OHLCV data and exports portfolio results.

The project is intended for research, not live trading. The synthetic demo is explicitly fictional and seeded for reproducibility; it exercises the software without requiring broker credentials or market-data subscriptions.

## Current project status

- Python 3.12+ is required; the project was tested with Python 3.14.
- The workspace is configured for a local project environment at `.venv`.
- The app ships with a Streamlit dashboard and a command-line backtest runner.
- Data ingestion supports both synthetic/demo data and real market data downloads from yfinance or Kite.
- Kite API keys and access tokens persist in the ignored `data/private/kite_credentials.json` file and are restored when the Kite menu opens; disconnect deletes the file. API secrets and one-time request tokens are not persisted.
- `scripts/download_kite_minutes.py` downloads one-minute OHLCV for the root `MW-NIFTY-100*.csv` stock list using saved Kite credentials, with optional NIFTY 50 benchmark inclusion. Requests default to a one-second pause, configurable with `--delay`; HTTP 429 retries use longer cooldowns.
- Optional dependencies are split into `dev`, `market-data`, and `kite-data` extras via `pyproject.toml`.
- Test coverage exists under `tests/` and is run with `pytest`; linting is configured with `ruff`.
- The codebase is organized into `trading_system/`, `frontend/`, `config/`, `scripts/`, `docs/`, and `examples/`.

## Repository structure

- `trading_system/` — strategy engine, data validation, indicators, regime logic, portfolio sizing, backtest runner, reports.
- `frontend/app.py` — Streamlit dashboard UI for running backtests and exploring output.
- `config/` — YAML settings and historical/universe definitions.
- `scripts/` — historical-data download utilities.
- `tests/` — unit and integration checks for indicators, features, risk logic, fills, engine behavior, and UI-facing validations.
- `examples/sample_output/` — reproducible sample outputs for synthetic daily backtests.
- `docs/` — supporting documentation.

## Working conventions

- Prefer the project-local environment instead of the system Python. Use `.venv` or an equivalent `uv` environment.
- Keep changes aligned with the modular architecture: data ingestion and validation, strategy logic, risk management, and backtesting/reporting remain separate concerns.
- Preserve the project’s research semantics: data is treated as causal, date-aware, and exchange-calendar filtered; no inferred or synthetic missing bars are created.
- When changing strategy logic, keep the signal interface and configuration model consistent with `config/settings.yaml` and the strategy registry.
- For market-data workflows, respect the documented input contracts and the demo-data warning: synthetic results are not a trading edge and are not live-market evidence.

## Setup and common commands

### Local environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Or with uv:

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
uv sync --extra dev
```

### Run the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.address 127.0.0.1
```

Alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-dashboard.ps1
```

### Run the CLI backtest suite

```powershell
.\.venv\Scripts\python.exe -m trading_system.main --demo --output output/demo
.\.venv\Scripts\python.exe -m trading_system.main --demo --mode intraday --output output/intraday
.\.venv\Scripts\python.exe -m trading_system.main --demo --mode hybrid --output output/hybrid
```

### Real-data download and validation

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market-data]"
.\.venv\Scripts\python.exe scripts/download_historical.py --universe config/universe_nse_example.csv --start 2012-01-01 --output data/private/yfinance
```

For Kite data:

```powershell
uv pip install --python .venv\Scripts\python.exe -e ".[kite-data]"
```

## Data contracts and requirements

- Daily and intraday CSVs are expected to use columns shaped as `timestamp,symbol,open,high,low,close,volume`.
- Optional daily fields include `adjusted_close` and `turnover`.
- Timestamps are interpreted in Asia/Kolkata time.
- Daily data uses a 15:30 IST close; intraday data uses bar-close timestamps with minute-level and five-minute bar conventions.
- Inputs must be cleaned of missing, duplicate, invalid, or out-of-session bars; the engine rejects inconsistent or incomplete data.
- For research runs, the app expects a valid universe file with `symbol,sector` and optional membership metadata.

## Operational notes for contributors

- The repository is a research app with deterministic demo behavior and optional live-data access paths; use the demo when validating changes unless a real-data workflow is specifically required.
- Keep generated outputs in ignored directories such as `data/private/` and `output/` unless the file is intentionally part of the repository.
- Do not hardcode credentials or store any Kite or broker secrets in tracked files.
- If you add or modify functionality, update the relevant README documentation and align the implementation with the CLI/dashboard workflow described in `README.md`.

## Validation expectations

- Run focused tests for affected modules with `pytest`.
- Use `ruff` for lint checks when modifying Python files.
- Validate project-level behavior using the demo-mode backtests before concluding work on strategy or portfolio changes.

This file captures the current project state as described in `README.md` and the repository structure; keep it current as the project evolves.
