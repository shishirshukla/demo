"""Run from the repository root: python scripts/download_historical.py --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_system.data.yfinance_downloader import main

if __name__ == "__main__":
    main()
