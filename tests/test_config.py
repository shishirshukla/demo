import io

import pytest

from trading_system.config import load_settings


def test_yaml_overrides_defaults_without_mutation():
    changed = load_settings(io.StringIO("portfolio:\n  initial_capital: 200000\n"))
    assert changed["portfolio"]["initial_capital"] == 200000
    assert changed["portfolio"]["max_positions"] == 3
    assert load_settings()["portfolio"]["initial_capital"] == 150000


@pytest.mark.parametrize(
    "yaml",
    [
        "portfolio:\n  risk_per_trade: -0.1\n",
        "costs:\n  spread_bps: -1\n",
        "strategies:\n  weakness_breakdown:\n    force_exit: '15:30'\n",
        "strategies:\n  opening_momentum:\n    opening_range_minutes: 17\n",
    ],
)
def test_reject_invalid_research_parameters(yaml):
    with pytest.raises(ValueError):
        load_settings(io.StringIO(yaml))
