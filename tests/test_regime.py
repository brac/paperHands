"""Tests for the market-regime overlay — risk-on/off detection + the buy-dropping guard."""

from __future__ import annotations

import pandas as pd

from core.config import StrategyConfig
from core.contracts import ProposedOrder, ProposedPlan
from data.frame import INDEX_NAME
from strategy.regime import MarketRegime, compute_market_regime, enforce_regime


def _bars(closes: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=len(closes)), name=INDEX_NAME)
    return pd.DataFrame({"adj_close": closes}, index=idx)


# -- compute_market_regime ---------------------------------------------------------------
def test_risk_on_above_ma():
    regime = compute_market_regime(_bars([float(i) for i in range(1, 21)]), ma_window=10)
    assert regime.risk_on is True  # rising series: last (20) >= 10-bar MA


def test_risk_off_below_ma():
    regime = compute_market_regime(_bars([float(i) for i in range(20, 0, -1)]), ma_window=10)
    assert regime.risk_on is False  # falling series: last (1) < 10-bar MA


def test_fails_open_on_short_history():
    # Too few bars to form the MA -> a missing signal must not suppress trading.
    assert compute_market_regime(_bars([1.0, 2.0, 3.0]), ma_window=10).risk_on is True


# -- enforce_regime ----------------------------------------------------------------------
_PLAN = ProposedPlan(orders=(
    ProposedOrder("buy", "AAA", target_weight=0.1, conviction=0.5),
    ProposedOrder("sell", "BBB"),
))
_RISK_OFF = MarketRegime(risk_on=False)
_RISK_ON = MarketRegime(risk_on=True)
_ON = StrategyConfig(regime_filter_enabled=True)
_OFF = StrategyConfig()  # filter disabled (default)


def test_drops_buys_when_enabled_and_risk_off():
    out = enforce_regime(_PLAN, _RISK_OFF, _ON)
    assert [(o.action, o.symbol) for o in out.orders] == [("sell", "BBB")]


def test_keeps_plan_when_risk_on():
    assert enforce_regime(_PLAN, _RISK_ON, _ON).orders == _PLAN.orders


def test_keeps_plan_when_filter_disabled():
    assert enforce_regime(_PLAN, _RISK_OFF, _OFF).orders == _PLAN.orders


def test_keeps_plan_when_regime_unknown():
    assert enforce_regime(_PLAN, None, _ON).orders == _PLAN.orders
