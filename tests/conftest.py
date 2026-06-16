"""Shared fixtures for the risk-gate and smoke tests."""

from __future__ import annotations

import pytest

from core.contracts import AccountState, MarketContext, Position
from risk.params import RiskParams


@pytest.fixture
def params() -> RiskParams:
    return RiskParams(
        max_position_pct=0.20,
        max_positions=3,
        min_price=5.0,
        min_avg_dollar_volume=1_000_000.0,
        daily_loss_limit=0.05,
    )


@pytest.fixture
def account() -> AccountState:
    return AccountState(
        cash=10_000.0,
        equity=10_000.0,
        buying_power=10_000.0,
        positions=(Position("HELD", qty=10.0, avg_price=50.0),),
        day_pnl=0.0,
    )


@pytest.fixture
def market() -> MarketContext:
    # All liquid, all above the price floor.
    return MarketContext(
        prices={"AAA": 100.0, "BBB": 200.0, "CCC": 50.0, "HELD": 50.0},
        avg_dollar_volume={"AAA": 5e9, "BBB": 4e9, "CCC": 2e9, "HELD": 1e9},
    )
