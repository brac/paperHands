"""Tests for the ETF rebalancer strategy (pure) and its config validation.

Offline and deterministic — ``rebalance_propose`` is pure over SignalSet prices, positions,
and cash. Covers the drift/schedule/both triggers, current-weight math, exit of non-target
holdings, and the regime de-risk overlay.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import RebalanceConfig, Settings, apply_mode_requirements
from core.contracts import Position
from signals.signalset import SignalSet
from strategy.rebalance import rebalance_propose
from strategy.regime import MarketRegime


def _signals(prices: dict[str, float]) -> dict[str, SignalSet]:
    return {s: SignalSet(symbol=s, price=p) for s, p in prices.items()}


_PRICES = {"SPY": 100.0, "BND": 100.0, "GLD": 100.0}
_CFG = RebalanceConfig(target_weights={"SPY": 0.6, "BND": 0.3, "GLD": 0.1})


def _orders_by_symbol(plan):
    return {o.symbol: o for o in plan.orders}


# -- triggers -------------------------------------------------------------------------
def test_schedule_always_rebalances_to_target():
    cfg = _CFG.model_copy(update={"trigger": "schedule"})
    plan = rebalance_propose(_signals(_PRICES), (), 100_000.0, cfg)
    orders = _orders_by_symbol(plan)
    assert set(orders) == {"SPY", "BND", "GLD"}
    assert all(o.action == "buy" for o in plan.orders)
    assert orders["SPY"].target_weight == pytest.approx(0.6)
    assert orders["BND"].target_weight == pytest.approx(0.3)
    assert orders["GLD"].target_weight == pytest.approx(0.1)


def test_drift_no_trade_when_within_band():
    cfg = _CFG.model_copy(update={"trigger": "drift", "drift_band": 0.05})
    # Positions exactly at target -> zero drift -> empty plan (low turnover).
    positions = (
        Position("SPY", qty=600.0, avg_price=100.0),  # 60% of 100k
        Position("BND", qty=300.0, avg_price=100.0),  # 30%
        Position("GLD", qty=100.0, avg_price=100.0),  # 10%
    )
    plan = rebalance_propose(_signals(_PRICES), positions, 0.0, cfg)
    assert plan.orders == ()


def test_drift_triggers_full_rebalance_on_breach():
    cfg = _CFG.model_copy(update={"trigger": "drift", "drift_band": 0.05})
    # GLD fully drained -> 10% drift (> band); cash holds the freed 10%.
    positions = (
        Position("SPY", qty=600.0, avg_price=100.0),
        Position("BND", qty=300.0, avg_price=100.0),
    )
    plan = rebalance_propose(_signals(_PRICES), positions, 10_000.0, cfg)
    orders = _orders_by_symbol(plan)
    assert set(orders) == {"SPY", "BND", "GLD"}  # full rebalance, not just the breached leg
    assert orders["GLD"].target_weight == pytest.approx(0.1)


def test_both_trigger_acts_only_on_breach():
    cfg = _CFG.model_copy(update={"trigger": "both", "drift_band": 0.05})
    at_target = (
        Position("SPY", qty=600.0, avg_price=100.0),
        Position("BND", qty=300.0, avg_price=100.0),
        Position("GLD", qty=100.0, avg_price=100.0),
    )
    assert rebalance_propose(_signals(_PRICES), at_target, 0.0, cfg).orders == ()


# -- current-weight math + exits ------------------------------------------------------
def test_held_symbol_left_universe_is_sold_to_zero():
    cfg = RebalanceConfig(target_weights={"SPY": 1.0}, defensive_symbol="",
                          trigger="schedule")
    prices = {"SPY": 100.0, "OLD": 50.0}
    positions = (Position("OLD", qty=100.0, avg_price=40.0),)  # value 5000
    plan = rebalance_propose(_signals(prices), positions, 5_000.0, cfg)
    orders = _orders_by_symbol(plan)
    assert orders["OLD"].action == "sell"
    assert orders["OLD"].target_weight == 0.0
    assert orders["SPY"].action == "buy"
    assert orders["SPY"].target_weight == pytest.approx(1.0)


def test_zero_equity_yields_empty_plan():
    cfg = _CFG.model_copy(update={"trigger": "schedule"})
    assert rebalance_propose(_signals(_PRICES), (), 0.0, cfg).orders == ()


# -- regime de-risk overlay -----------------------------------------------------------
def _derisk_cfg(**over):
    base = dict(
        target_weights={"SPY": 0.6, "BND": 0.3, "GLD": 0.1},
        equity_symbols=("SPY",),
        defensive_symbol="BND",
        regime_derisk_enabled=True,
        regime_derisk_shift=0.5,
        trigger="schedule",
    )
    base.update(over)
    return RebalanceConfig(**base)


def test_regime_derisk_scales_equity_into_defensive_when_risk_off():
    cfg = _derisk_cfg()
    risk_off = MarketRegime(risk_on=False)
    orders = _orders_by_symbol(rebalance_propose(_signals(_PRICES), (), 100_000.0, cfg,
                                                 regime=risk_off))
    assert orders["SPY"].target_weight == pytest.approx(0.3)  # 0.6 * (1 - 0.5)
    assert orders["BND"].target_weight == pytest.approx(0.6)  # 0.3 + freed 0.3
    assert orders["GLD"].target_weight == pytest.approx(0.1)  # untouched


def test_regime_derisk_noop_when_risk_on_or_disabled_or_no_regime():
    cfg = _derisk_cfg()
    on = MarketRegime(risk_on=True)
    for regime, c in [
        (on, cfg),                                            # risk-on
        (MarketRegime(risk_on=False), _derisk_cfg(regime_derisk_enabled=False)),  # disabled
        (None, cfg),                                          # no regime
    ]:
        orders = _orders_by_symbol(rebalance_propose(_signals(_PRICES), (), 100_000.0, c,
                                                     regime=regime))
        assert orders["SPY"].target_weight == pytest.approx(0.6)
        assert orders["BND"].target_weight == pytest.approx(0.3)


def test_regime_derisk_to_cash_when_no_defensive_symbol():
    cfg = _derisk_cfg(defensive_symbol="")
    risk_off = MarketRegime(risk_on=False)
    orders = _orders_by_symbol(rebalance_propose(_signals(_PRICES), (), 100_000.0, cfg,
                                                 regime=risk_off))
    assert orders["SPY"].target_weight == pytest.approx(0.3)
    assert orders["BND"].target_weight == pytest.approx(0.3)  # freed weight went to cash


# -- config validation ----------------------------------------------------------------
def test_weights_summing_above_one_rejected():
    with pytest.raises(ValidationError):
        RebalanceConfig(target_weights={"SPY": 0.7, "BND": 0.5})


def test_weights_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RebalanceConfig(target_weights={"SPY": 0.0})
    with pytest.raises(ValidationError):
        RebalanceConfig(target_weights={"SPY": 1.5})


def test_empty_weights_rejected():
    with pytest.raises(ValidationError):
        RebalanceConfig(target_weights={})


def test_universe_includes_defensive_symbol():
    cfg = RebalanceConfig(target_weights={"SPY": 0.6, "BND": 0.4}, defensive_symbol="GLD")
    assert cfg.universe() == ("BND", "GLD", "SPY")


def test_weights_summing_below_one_is_allowed():
    cfg = RebalanceConfig(target_weights={"SPY": 0.5, "BND": 0.3})  # 20% intentional cash
    assert sum(cfg.target_weights.values()) == pytest.approx(0.8)


# -- mode requirements coercion (so --mode rebalance "just works") --------------------
def test_apply_mode_requirements_coerces_rebalance_plumbing():
    # Even a --mode override (model_copy skips validators) must end up target-weight + bypass.
    settings = Settings().model_copy(update={"strategy_mode": "rebalance"})
    assert settings.risk.sizing == "new-dollars"  # not yet coerced
    fixed = apply_mode_requirements(settings)
    assert fixed.risk.sizing == "target-weight"
    assert fixed.engine.screen_bypass is True
    # Per-symbol cap is lifted to fit the basket (default 0.20 < 0.60 SPY target).
    assert fixed.risk.max_position_pct >= max(fixed.rebalance.target_weights.values())


def test_apply_mode_requirements_noop_for_legacy_modes():
    settings = Settings()  # default rules-only
    assert apply_mode_requirements(settings) is settings
    assert settings.risk.sizing == "new-dollars"
