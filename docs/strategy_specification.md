# NIFTY Top-150 Regime-Based Trading System
## Codex Implementation Specification

Version: 1.0  
Target language: Python 3.12+  
Primary use: Automated intraday and swing trading research/backtesting, with optional live execution later  
Capital base: INR 150,000  
Market: NSE India  
Universe: Approx. top 150 liquid NIFTY stocks

---

## 1. Objective

Build a modular Python trading system for the top ~150 liquid NSE-listed stocks.

The system must:

1. Classify the overall market into:
   - Bullish
   - Bearish
   - Flat / Transitional
2. Activate only strategies appropriate for the current regime.
3. Support both swing and intraday strategies.
4. Apply portfolio-level risk limits before order generation.
5. Backtest all strategies with realistic transaction costs and slippage.
6. Avoid parameter over-optimization and curve fitting.
7. Support walk-forward and out-of-sample evaluation.
8. Keep research, backtesting, portfolio management, and broker execution separated.

The five strategies are:

1. Relative Strength Momentum Pullback
2. Volatility Compression Breakout
3. Relative Weakness Breakdown
4. VWAP Mean Reversion
5. Regime-Filtered Opening Momentum

---

# 2. Universe Definition

Preferred universe:

- NIFTY 100 constituents
- Plus the largest 50 eligible stocks from NIFTY Midcap 150

Alternative implementation:

- Load a configurable CSV containing the target 150 symbols.

The application must never hard-code the universe into strategy logic.

Example:

```text
config/universe.csv
```

Suggested columns:

```text
symbol
sector
index_group
lot_size
is_fno
active_from
active_to
```

For historical backtesting, support constituent membership dates to reduce survivorship bias.

---

# 3. Daily Liquidity Filters

Before generating signals, exclude stocks failing liquidity constraints.

Default filters:

```text
price >= 50 INR
20-day average traded value >= 20 crore INR
minimum 20-day average volume configurable
```

For intraday strategies, optionally add:

```text
maximum median bid-ask spread
minimum median 5-minute turnover
```

All liquidity thresholds must be configurable.

---

# 4. System Architecture

Use the following suggested structure:

```text
trading_system/
│
├── config/
│   ├── settings.yaml
│   ├── universe.csv
│   └── logging.yaml
│
├── data/
│   ├── historical.py
│   ├── intraday.py
│   ├── corporate_actions.py
│   ├── benchmark.py
│   └── validation.py
│
├── indicators/
│   ├── moving_average.py
│   ├── atr.py
│   ├── adx.py
│   ├── momentum.py
│   ├── breadth.py
│   ├── vwap.py
│   └── volatility.py
│
├── regime/
│   └── classifier.py
│
├── strategies/
│   ├── base.py
│   ├── momentum_pullback.py
│   ├── volatility_breakout.py
│   ├── weakness_breakdown.py
│   ├── vwap_reversion.py
│   └── opening_momentum.py
│
├── portfolio/
│   ├── position_sizing.py
│   ├── exposure.py
│   ├── correlation.py
│   └── risk_manager.py
│
├── backtest/
│   ├── engine.py
│   ├── fills.py
│   ├── costs.py
│   ├── walk_forward.py
│   └── metrics.py
│
├── execution/
│   ├── broker_base.py
│   ├── paper_broker.py
│   └── live_broker.py
│
├── models/
│   ├── signal.py
│   ├── order.py
│   ├── trade.py
│   └── position.py
│
├── reports/
│   └── performance.py
│
├── tests/
│
└── main.py
```

---

# 5. Common Data Requirements

## Daily OHLCV

Required fields:

```text
timestamp
symbol
open
high
low
close
volume
adjusted_close
```

Recommended additional data:

```text
turnover
corporate_action_flag
sector
benchmark_membership
```

## Intraday OHLCV

Preferred frequency:

```text
5-minute bars
```

Required fields:

```text
timestamp
symbol
open
high
low
close
volume
```

Trading timezone:

```text
Asia/Kolkata
```

NSE cash market session:

```text
09:15 to 15:30 IST
```

Do not assume bars outside this period are tradable.

---

# 6. Market Regime Classifier

Benchmark:

```text
NIFTY 50
```

Required indicators:

```text
EMA50
EMA200
EMA50 slope
ADX14
market breadth
```

Market breadth:

```text
percentage of tradable universe with close > SMA50
```

## Bullish Regime

Default rule:

```text
NIFTY close > EMA50
EMA50 > EMA200
EMA50 slope > 0
breadth > 55%
```

## Bearish Regime

Default rule:

```text
NIFTY close < EMA50
EMA50 < EMA200
EMA50 slope < 0
breadth < 45%
```

## Flat / Transitional Regime

Anything not meeting Bullish or Bearish conditions.

Add secondary regime label using ADX:

```text
ADX >= 20 => directional
ADX < 20 => non-directional
```

Do not optimize thresholds to unusually precise values.

Recommended configuration:

```yaml
regime:
  breadth_bull: 0.55
  breadth_bear: 0.45
  adx_directional: 20
  ema_fast: 50
  ema_slow: 200
```

---

# 7. Strategy 1: Relative Strength Momentum Pullback

## Intended Regime

```text
Bullish
```

## Timeframe

```text
Swing
```

Typical holding period:

```text
2 to 10 trading days
```

## Hypothesis

Stocks showing persistent relative strength versus the broad market tend to continue outperforming over short-to-medium horizons.

A controlled pullback can provide a better entry than buying an extended breakout.

## Indicators

For every stock:

```text
20-day return
60-day return
NIFTY 20-day return
NIFTY 60-day return
relative strength 20
relative strength 60
EMA20
SMA50
SMA200
ATR14
10-day high
```

Definitions:

```python
return_20 = close / close.shift(20) - 1
return_60 = close / close.shift(60) - 1

rs20 = stock_return_20 - nifty_return_20
rs60 = stock_return_60 - nifty_return_60
```

Rank `rs20` and `rs60` cross-sectionally across the tradable universe.

## Candidate Filter

```text
rs20 percentile >= 80
rs60 percentile >= 70
close > SMA50
SMA50 > SMA200
market regime == BULL
```

## Pullback Definition

Default:

```text
stock is 2% to 5% below its prior 10-day high
price remains above EMA20 or SMA50
relative-strength rank remains in top 20%
```

Implementation should make pullback bounds configurable.

## Entry

Enter long when:

```text
candidate is in valid pullback
AND today's close > previous day's high
```

For event-driven intraday execution of a daily signal, allow next-session market/open or stop-entry simulation.

Backtest both consistently.

## Initial Stop

Use:

```text
entry - 2 * ATR14
```

or recent swing low if that produces a tighter but valid technical stop.

Implementation:

```text
stop = max(recent_swing_low, entry - 2 * ATR14)
```

Ensure stop remains below entry.

## Exit

Exit on the first occurring event:

```text
2.5 ATR trailing stop
OR
close < EMA20 for 2 consecutive sessions
OR
10 trading-day maximum holding period
```

No fixed percentage profit target.

---

# 8. Strategy 2: Volatility Compression Breakout

## Intended Regime

```text
Bullish
or
Directional Transitional regime
```

## Timeframe

```text
Swing
```

Typical holding period:

```text
1 to 7 trading days
```

## Hypothesis

Periods of unusually low realized volatility are often followed by volatility expansion.

When the breakout direction aligns with an established broader trend, continuation probability may improve.

## Indicators

```text
ATR14 / close
20-day realized volatility
20-day price range
20-day high
SMA50
SMA200
20-day average volume
```

## Candidate Filter

```text
20-day volatility percentile <= 30%
ATR percentile <= 40%
close > SMA50
SMA50 > SMA200
market regime is not BEAR
```

## Breakout Confirmation

```text
close > prior 20-day high
volume >= 1.5 * 20-day average volume
```

Use shifted rolling highs to prevent lookahead bias.

Example:

```python
prior_20d_high = high.rolling(20).max().shift(1)
```

## Entry

```text
entry above prior 20-day high + 0.1 * ATR14
```

For daily-bar backtests, define a deterministic fill model.

## Initial Stop

```text
entry - 1.5 * ATR14
```

## Exit

Exit on first occurrence:

```text
close below prior 10-day low
OR
2.5 ATR trailing stop
OR
7 trading-day maximum holding period
```

---

# 9. Strategy 3: Relative Weakness Breakdown

## Intended Regime

```text
Bearish
```

## Timeframe

Primary implementation:

```text
Intraday
```

Optional future implementation:

```text
F&O swing
```

Do not carry naked cash-equity short positions overnight.

## Hypothesis

Stocks materially underperforming the market during bearish conditions may continue to experience downside pressure, particularly after support breaks with increased participation.

## Indicators

```text
20-day stock return
20-day NIFTY return
relative return
SMA20
SMA50
VWAP
ATR14
10-day support
relative volume
```

## Candidate Filter

```text
20-day relative return percentile <= 20
close < SMA20
close < SMA50
SMA20 < SMA50
market regime == BEAR
```

## Intraday Setup

Previous support:

```python
support = low.rolling(10).min().shift(1)
```

Signal:

```text
price < support
price < VWAP
relative volume >= 1.5
```

Relative volume may be defined as:

```text
current cumulative volume /
historical average cumulative volume at same time of day
```

## Entry

Enter short after breakdown confirmation.

Prefer 5-minute close below support rather than single-tick penetration.

## Stop

Default:

```text
min(
    VWAP-based invalidation distance,
    entry + 1.5 * ATR-derived intraday distance
)
```

Ensure implementation avoids nonsensical stop placement.

## Exit

Exit on first occurrence:

```text
2.5R profit objective
OR
5-minute close above VWAP
OR
15:10 IST
```

No overnight positions.

---

# 10. Strategy 4: VWAP Mean Reversion

## Intended Regime

```text
Flat / non-directional
```

## Timeframe

```text
Intraday
```

## Hypothesis

In non-trending markets, temporary stock-specific dislocations away from fair intraday value can mean-revert, particularly when the move is not explained by broad market or sector movement.

## Regime Requirements

```text
market regime == FLAT
ADX14 < 20
market breadth approximately between 45% and 55%
```

## Indicators

For each stock:

```text
session VWAP
intraday rolling standard deviation
RSI5
sector return
NIFTY return
stock return
```

Preferred residual move:

```text
stock_return - sector_return
```

or:

```text
stock_return - beta_adjusted_market_return
```

## Long Setup

```text
price < VWAP - 2 * intraday_std
RSI5 < 25
large negative residual move
no detected directional market breakdown
```

Require reversal confirmation:

```text
5-minute candle closes above previous candle high
```

## Short Setup

Symmetrical:

```text
price > VWAP + 2 * intraday_std
RSI5 > 75
large positive residual move
5-minute candle closes below previous candle low
```

## Target

Primary:

```text
VWAP
```

Optional partial exit:

```text
50% at 1 standard deviation from VWAP
remaining position at VWAP
```

Do not introduce partial exits in version 1 unless needed.

## Stop

For long:

```text
recent swing low - 0.25 * ATR-derived intraday buffer
```

For short:

```text
recent swing high + 0.25 * ATR-derived intraday buffer
```

## Time Exit

Close all positions by:

```text
15:10 IST
```

---

# 11. Strategy 5: Regime-Filtered Opening Momentum

## Intended Regime

```text
Strong Bullish
or
Strong Bearish
```

## Timeframe

```text
Intraday
```

## Opening Range

Default:

```text
09:15 to 09:30 IST
```

Define:

```python
OR_high = max(high from 09:15 through 09:30)
OR_low = min(low from 09:15 through 09:30)
```

Ensure no later data enters opening-range calculations.

## Bullish Setup

```text
market regime == BULL
NIFTY > previous close
NIFTY > VWAP
market breadth > 60%
stock > previous close
stock > VWAP
relative volume >= 1.5
stock relative strength > 0
```

Entry:

```text
5-minute close > OR_high
```

## Bearish Setup

Reverse all directional conditions:

```text
market regime == BEAR
NIFTY < previous close
NIFTY < VWAP
market breadth < 40%
stock < previous close
stock < VWAP
relative volume >= 1.5
stock relative strength < 0
```

Entry:

```text
5-minute close < OR_low
```

## Stop

Use one consistent method in version 1:

```text
opening-range midpoint
```

Optional later alternative:

```text
VWAP
```

Do not optimize between many stop variants during initial research.

## Exit

Default:

```text
2R target
OR
VWAP invalidation
OR
15:10 IST
```

---

# 12. Strategy Activation Matrix

```text
Strong Bull:
  Strategy 1 = ON
  Strategy 2 = ON
  Strategy 3 = OFF
  Strategy 4 = OFF
  Strategy 5 Long = ON

Mild Bull:
  Strategy 1 = ON
  Strategy 2 = ON
  Strategy 3 = OFF
  Strategy 4 = optional
  Strategy 5 Long = optional

Flat:
  Strategy 1 = OFF
  Strategy 2 = OFF or low priority
  Strategy 3 = OFF
  Strategy 4 = ON
  Strategy 5 = OFF

Mild Bear:
  Strategy 1 = OFF
  Strategy 2 = OFF
  Strategy 3 = ON
  Strategy 4 = optional
  Strategy 5 Short = optional

Strong Bear:
  Strategy 1 = OFF
  Strategy 2 = OFF
  Strategy 3 = ON
  Strategy 4 = OFF
  Strategy 5 Short = ON
```

Version 1 may simplify regimes to:

```text
BULL
FLAT
BEAR
```

---

# 13. Capital and Risk Management

Starting capital:

```text
150,000 INR
```

## Risk Per Trade

Default:

```text
0.50% of equity
```

At initial capital:

```text
750 INR
```

## Daily Loss Limit

```text
1.5% of start-of-day equity
```

Initial equivalent:

```text
2,250 INR
```

After daily loss limit is hit:

```text
no new trades
```

Existing trades should follow normal stops.

## Weekly Loss Limit

```text
4% of start-of-week equity
```

After threshold is hit:

```text
disable new entries until next trading week
```

## Maximum Concurrent Positions

```text
3
```

## Maximum Capital Per Stock

Default:

```text
30% of total equity
```

At initial equity:

```text
45,000 INR
```

## Position Sizing

```python
risk_amount = account_equity * risk_per_trade

risk_per_share = abs(entry_price - stop_price)

qty_by_risk = floor(risk_amount / risk_per_share)

qty_by_capital = floor(max_capital_per_stock / entry_price)

quantity = min(qty_by_risk, qty_by_capital)
```

Reject trade if:

```text
quantity <= 0
```

---

# 14. Sector Exposure Controls

Default:

```text
maximum 2 simultaneous positions from the same sector
```

Add portfolio correlation controls later.

Suggested optional rule:

```text
if pairwise 60-day return correlation > 0.75:
    reduce or reject duplicate directional exposure
```

Do not use correlation controls in initial baseline unless data quality is reliable.

---

# 15. Signal Prioritization

When more signals exist than available position slots, rank candidates.

Suggested ranking:

## Strategy 1

```text
higher RS20 rank
higher RS60 rank
smaller pullback depth
```

## Strategy 2

```text
stronger breakout volume
lower pre-breakout volatility percentile
higher relative strength
```

## Strategy 3

```text
weaker relative strength
larger relative volume
cleaner support break
```

## Strategy 4

```text
larger residual deviation from VWAP
stronger reversal confirmation
lower market ADX
```

## Strategy 5

```text
higher relative volume
stronger market breadth alignment
stronger relative strength/weakness
```

Ranking logic must be deterministic.

---

# 16. Transaction Costs

Backtesting must include:

```text
brokerage
STT
exchange transaction charges
SEBI charges
GST
stamp duty
bid-ask spread
slippage
```

Implement the cost model in one separate module.

Slippage scenarios:

```text
3 basis points
5 basis points
10 basis points
```

Every backtest report must show performance under all three scenarios.

Do not evaluate intraday strategies without costs.

---

# 17. Fill Modeling

Prevent unrealistic fills.

Examples:

- Daily long stop order:
  - fill at max(stop_price, next available tradable price)
- Intraday breakout:
  - use next bar open or stop-trigger fill model
- Stops:
  - account for gaps beyond stop price
- Limit orders:
  - only fill if bar trading range permits

Do not assume exact fills at requested price when market gaps through an order.

---

# 18. Lookahead-Bias Rules

All indicators and rolling thresholds must use only information available at signal time.

Examples:

Correct:

```python
prior_high = high.rolling(20).max().shift(1)
```

Incorrect:

```python
prior_high = high.rolling(20).max()
```

when today's high is included before today's signal is generated.

Use strict timestamp ordering for intraday strategies.

---

# 19. Backtest Period Design

Preferred full data span:

```text
2012 to latest available date
```

Suggested chronological split:

```text
2012-2019 = development
2020-2022 = validation
2023-2024 = out-of-sample
2025 onward = final untouched test
```

If historical data availability differs, preserve the same principle:

```text
development
validation
out-of-sample
final untouched test
```

Never randomly shuffle financial time series.

---

# 20. Walk-Forward Validation

Implement walk-forward analysis.

Example:

```text
train: 5 years
validate: 1 year
roll forward: 1 year
```

Parameter selection must be limited to broad parameter families.

Do not perform unrestricted optimization.

---

# 21. Parameter Robustness Tests

Test neighborhoods rather than searching for one best value.

Examples:

```text
momentum lookback:
20
40
60

ATR stop:
1.5
2.0
2.5

opening range:
15 minutes
30 minutes

RS percentile:
70
80
90
```

Robust behavior:

```text
nearby parameters produce similar economics
```

Reject strategies where:

```text
one exact value performs exceptionally
adjacent values perform poorly
```

---

# 22. Backtest Metrics

Generate at minimum:

```text
CAGR
total return
annual return
Sharpe ratio
Sortino ratio
maximum drawdown
Calmar ratio
profit factor
expectancy
win rate
average winner
average loser
average R multiple
median R multiple
trades per year
turnover
exposure percentage
average holding period
maximum consecutive losses
maximum consecutive wins
monthly returns
yearly returns
```

Expectancy:

```text
E = P(win) * AvgWin - P(loss) * AvgLoss
```

---

# 23. Strategy-Level Reporting

For every strategy report:

```text
performance by year
performance by regime
performance by stock
performance by sector
performance by volatility quartile
performance by liquidity quartile
performance by long/short direction
performance under 3/5/10 bps slippage
```

Also report:

```text
top 10 contributors
bottom 10 contributors
```

The system must identify whether performance comes from only a few symbols.

---

# 24. Portfolio-Level Reporting

Combine all enabled strategies and report:

```text
portfolio equity curve
drawdown curve
strategy contribution
capital utilization
strategy overlap
sector exposure
daily turnover
monthly P&L
rolling Sharpe
rolling drawdown
```

---

# 25. Suggested Core Data Models

## Signal

```python
@dataclass
class Signal:
    timestamp: datetime
    symbol: str
    strategy: str
    side: Literal["LONG", "SHORT"]
    entry_price: float
    stop_price: float
    target_price: float | None
    score: float
    metadata: dict
```

## Order

```python
@dataclass
class Order:
    timestamp: datetime
    symbol: str
    side: str
    quantity: int
    order_type: str
    price: float | None
    stop_price: float | None
    strategy: str
```

## Trade

```python
@dataclass
class Trade:
    symbol: str
    strategy: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    r_multiple: float
```

---

# 26. Strategy Interface

All strategies should inherit from a common base class.

Example:

```python
class BaseStrategy(ABC):

    @abstractmethod
    def generate_signals(
        self,
        market_data,
        benchmark_data,
        regime,
        timestamp
    ) -> list[Signal]:
        ...
```

Strategies must not execute broker orders directly.

---

# 27. Risk Manager Interface

Example responsibilities:

```text
daily loss check
weekly loss check
maximum positions
sector exposure
capital-per-stock limit
position sizing
duplicate symbol check
correlated exposure check
```

Suggested method:

```python
approved_signals = risk_manager.filter_and_size(
    signals=signals,
    portfolio=portfolio,
    account_equity=equity,
    timestamp=timestamp
)
```

---

# 28. Main Research Loop

Conceptual logic:

```python
regime = regime_classifier.classify(
    benchmark_data=nifty_data,
    universe_data=universe_data,
    timestamp=current_time,
)

signals = []

if regime == "BULL":
    signals += strategy1.generate_signals(...)
    signals += strategy2.generate_signals(...)
    signals += strategy5.generate_long_signals(...)

elif regime == "BEAR":
    signals += strategy3.generate_signals(...)
    signals += strategy5.generate_short_signals(...)

else:
    signals += strategy4.generate_signals(...)

approved_orders = risk_manager.process(signals)

execution_engine.submit(approved_orders)
```

---

# 29. Configuration Philosophy

All parameters should live in YAML.

Example:

```yaml
portfolio:
  initial_capital: 150000
  risk_per_trade: 0.005
  daily_loss_limit: 0.015
  weekly_loss_limit: 0.04
  max_positions: 3
  max_stock_allocation: 0.30

strategy_1:
  rs20_percentile: 0.80
  rs60_percentile: 0.70
  pullback_min: 0.02
  pullback_max: 0.05
  atr_stop: 2.0
  atr_trail: 2.5
  max_holding_days: 10

strategy_2:
  volatility_percentile: 0.30
  atr_percentile: 0.40
  breakout_lookback: 20
  volume_multiplier: 1.5
  stop_atr: 1.5
  trail_atr: 2.5

strategy_3:
  rs_percentile: 0.20
  support_lookback: 10
  relative_volume: 1.5
  max_r_target: 2.5
  force_exit: "15:10"

strategy_4:
  adx_max: 20
  deviation_std: 2.0
  rsi_long: 25
  rsi_short: 75
  force_exit: "15:10"

strategy_5:
  opening_range_minutes: 15
  relative_volume: 1.5
  bull_breadth: 0.60
  bear_breadth: 0.40
  target_r: 2.0
  force_exit: "15:10"
```

---

# 30. Anti-Curve-Fitting Rules

Codex must preserve these constraints:

1. Do not optimize every parameter.
2. Do not introduce indicators merely because they improve historical CAGR.
3. Do not use machine learning in version 1.
4. Do not dynamically fit rules to the final test period.
5. Do not use random train/test splits.
6. Do not choose parameters solely by maximum Sharpe.
7. Prefer simple rules with economic intuition.
8. Reject fragile parameter peaks.
9. Report losing periods rather than hiding them.
10. Include all trading costs.

---

# 31. Implementation Phases

## Phase 1

Build:

```text
data layer
indicator library
regime classifier
daily liquidity filter
strategy 1
strategy 2
risk manager
daily backtester
performance report
```

## Phase 2

Add:

```text
5-minute data ingestion
VWAP
relative volume
strategy 3
strategy 4
strategy 5
intraday backtester
```

## Phase 3

Add:

```text
walk-forward engine
parameter robustness testing
portfolio strategy combination
sector/correlation controls
Monte Carlo trade-sequence analysis
```

## Phase 4

Add:

```text
paper broker
broker adapter
live market data
order management
persistent state
recovery after restart
alerting
```

Do not begin live trading before the research and paper-trading phases pass validation.

---

# 32. Required Unit Tests

At minimum test:

```text
ATR calculation
EMA/SMA calculation
ADX calculation
VWAP calculation
breadth calculation
relative strength ranking
regime classification
position sizing
daily loss limit
weekly loss limit
sector exposure
opening range calculation
no-lookahead rolling highs/lows
transaction cost calculation
gap-through-stop handling
forced intraday exit
```

---

# 33. Required Acceptance Criteria

The initial implementation is complete only when:

- all five strategies can be enabled/disabled from configuration
- regime classification is deterministic
- backtests include transaction costs
- position sizing uses risk, not fixed quantity
- no strategy can bypass risk controls
- intraday positions are closed before session end
- all rolling indicators are lookahead-safe
- reports are reproducible
- backtest results can be exported to CSV/JSON
- strategy parameters are stored outside source code
- tests pass
- code includes logging and exception handling

---

# 34. Deliverables Expected From Codex

Codex should create:

```text
complete Python package
requirements.txt or pyproject.toml
settings.yaml
sample universe.csv
README.md
unit tests
example backtest script
performance report generator
sample output CSVs
```

Preferred libraries:

```text
pandas
numpy
scipy
pyyaml
matplotlib
pytest
pydantic
```

Optional:

```text
polars
numba
statsmodels
```

Do not depend on proprietary libraries for the core research engine.

---

# 35. Final Design Principle

The system should not try to predict every market move.

Its intended edge is:

```text
identify market regime
+
activate a strategy suited to that regime
+
trade only liquid stocks
+
size every position by risk
+
control correlated exposure
+
include realistic trading costs
+
validate across multiple independent periods
```

The strategy engine should remain simple, auditable, and reproducible.
