import copy
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_settings(path=None):
    with open(ROOT / "config/settings.yaml", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if path is not None:
        if hasattr(path, "read"):
            overrides = yaml.safe_load(path)
        else:
            with open(path, encoding="utf-8") as handle:
                overrides = yaml.safe_load(handle)
        if not isinstance(overrides, dict):
            raise ValueError("Settings YAML must contain a mapping")

        def merge(target, source):
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = value

        merge(config, overrides)
    p = config["portfolio"]
    for key in (
        "risk_per_trade",
        "daily_loss_limit",
        "weekly_loss_limit",
        "max_stock_allocation",
    ):
        if not 0 < p[key] <= 1:
            raise ValueError(f"portfolio.{key} must be between 0 and 1")
    if p["initial_capital"] <= 0 or p["max_positions"] < 1:
        raise ValueError("Capital and position limits must be positive")
    for key in ["max_positions", "max_sector_positions"]:
        if not isinstance(p[key], int) or p[key] < 1:
            raise ValueError(f"portfolio.{key} must be a positive integer")
    if not 0 < p["max_gross_exposure"] <= 1:
        raise ValueError("Baseline cash research allows max_gross_exposure up to 1.0")
    for key, value in config["costs"].items():
        values = value if isinstance(value, list) else [value]
        if not all(
            isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values
        ):
            raise ValueError(f"costs.{key} must contain finite non-negative values")
    for name, params in config["strategies"].items():
        if not isinstance(params.get("enabled"), bool):
            raise ValueError(f"strategies.{name}.enabled must be true or false")
        if "force_exit" in params:
            try:
                hour, minute = map(int, params["force_exit"].split(":"))
                clock = hour * 60 + minute
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("force_exit must be HH:MM") from exc
            if (
                not 560 <= clock <= 910
                or minute % 5
                or params["force_exit"] != f"{hour:02d}:{minute:02d}"
            ):
                raise ValueError(
                    "force_exit must be a 5-minute close timestamp from 09:20 to 15:10"
                )
    opening = config["strategies"]["opening_momentum"]["opening_range_minutes"]
    if opening not in [15, 30]:
        raise ValueError(
            "opening_range_minutes supports broad 15 or 30 minute variants"
        )
    return copy.deepcopy(config)
