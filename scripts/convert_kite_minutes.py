"""Run from the project root: python scripts/convert_kite_minutes.py --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_system.data.kite_conversion import main

if __name__ == "__main__":
    main()
