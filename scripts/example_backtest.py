from trading_system.backtest import BacktestEngine
from trading_system.config import load_settings
from trading_system.data import demo_dataset
from trading_system.reports import export_results

settings = load_settings()
data = demo_dataset()
results = [BacktestEngine(settings).run(data, bps) for bps in [3, 5, 10]]
export_results(results, "output/example", data)
