"""Run from the project root: python scripts/download_kite_minutes.py --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_system.data.kite_stock_downloader import main

if __name__ == "__main__":
    main()
