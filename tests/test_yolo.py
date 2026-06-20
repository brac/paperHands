"""Tests for the YOLO sleeve (pure) and its mode-requirement coercion.

Offline and deterministic — ``yolo_propose`` is pure over SignalSet hype components, positions,
and cash. Covers the hype ranking, top-N concentration, conviction vs equal weighting, the
per-name cap + redistribution, rotate-out / stop-loss exits, and the gate-cap coercion that
makes ``--mode yolo`` size correctly.
"""

from __future__ import annotations

import pytest

from core.config import Settings, YoloConfig, apply_mode_requirements
from core.contracts import Position
from signals.signalset import SignalSet
from strategy.regime import MarketRegime
from strategy.yolo import hype_score, yolo_propose


def _sig(symbol: str, *, price=10.0, roc=None, volume_spike=None, dist_from_high=None,
         social_score=None) -> SignalSet:
    return SignalSet(
        symbol=symbol, price=price, roc=roc, volume_spike=volume_spike,
        dist_from_high=dist_from_high, social_score=social_score,
    )


def _by_symbol(plan):
    return {o.symbol: o for o in plan.orders}


# -- hype_score -----------------------------------------------------------------------
def test_hype_score_clamps_negative_components_to_zero():
    cfg = YoloConfig()
    # Negative roc and below-average volume contribute nothing (hype is one-sided).
    cold = _sig("X", roc=-0.5, volume_spike=-0.9)
    assert hype_score(cold, cfg) == 0.0


def test_hype_score_blends_weighted_components():
    cfg = YoloConfig(momentum_weight=1.0, volume_weight=2.0, breakout_weight=0.0,
                     social_weight=0.0)
    s = _sig("X", roc=0.5, volume_spike=3.0)
    assert hype_score(s, cfg) == pytest.approx(1.0 * 0.5 + 2.0 * 3.0)


def test_hype_score_breakout_term_rewards_names_at_their_high():
    cfg = YoloConfig(momentum_weight=0.0, volume_weight=0.0, breakout_weight=1.0,
                     social_weight=0.0)
    at_high = _sig("AT", dist_from_high=0.0)      # 1 + 0 = 1.0
    below = _sig("LO", dist_from_high=-0.3)        # 1 - 0.3 = 0.7
    assert hype_score(at_high, cfg) == pytest.approx(1.0)
    assert hype_score(below, cfg) == pytest.approx(0.7)


# -- ranking + concentration ----------------------------------------------------------
def test_picks_top_n_hottest_and_excludes_cold_names():
    cfg = YoloConfig(top_n=2, max_position_pct=1.0)
    sigs = {
        "HOT": _sig("HOT", roc=0.8, volume_spike=4.0),
        "WARM": _sig("WARM", roc=0.3, volume_spike=1.0),
        "MILD": _sig("MILD", roc=0.1, volume_spike=0.2),
        "COLD": _sig("COLD", roc=-0.4, volume_spike=-0.5),  # zero score -> never bought
    }
    plan = yolo_propose(sigs, [], 10_000.0, cfg)
    orders = _by_symbol(plan)
    assert set(orders) == {"HOT", "WARM"}
    assert all(o.action == "buy" for o in plan.orders)


def test_conviction_weighting_favors_the_hotter_name():
    cfg = YoloConfig(top_n=2, max_position_pct=1.0, conviction_weighted=True,
                     volume_weight=1.0, momentum_weight=0.0, breakout_weight=0.0)
    sigs = {
        "HOT": _sig("HOT", volume_spike=3.0),
        "WARM": _sig("WARM", volume_spike=1.0),
    }
    orders = _by_symbol(yolo_propose(sigs, [], 10_000.0, cfg))
    assert orders["HOT"].target_weight > orders["WARM"].target_weight
    assert orders["HOT"].target_weight + orders["WARM"].target_weight == pytest.approx(1.0)


def test_equal_weighting_splits_evenly():
    cfg = YoloConfig(top_n=2, max_position_pct=1.0, conviction_weighted=False)
    sigs = {"A": _sig("A", roc=0.9), "B": _sig("B", roc=0.1)}
    orders = _by_symbol(yolo_propose(sigs, [], 10_000.0, cfg))
    assert orders["A"].target_weight == pytest.approx(0.5)
    assert orders["B"].target_weight == pytest.approx(0.5)


def test_per_name_cap_enforced_and_slack_redistributed():
    cfg = YoloConfig(top_n=2, max_position_pct=0.6, conviction_weighted=True,
                     volume_weight=1.0, momentum_weight=0.0, breakout_weight=0.0)
    sigs = {"HOT": _sig("HOT", volume_spike=9.0), "WARM": _sig("WARM", volume_spike=1.0)}
    orders = _by_symbol(yolo_propose(sigs, [], 10_000.0, cfg))
    # HOT would exceed the 0.6 cap; it is clamped and the freed weight flows to WARM.
    assert orders["HOT"].target_weight == pytest.approx(0.6)
    assert orders["WARM"].target_weight == pytest.approx(0.4)


# -- exits ----------------------------------------------------------------------------
def test_rotates_out_of_held_name_no_longer_hot():
    cfg = YoloConfig(top_n=1, max_position_pct=1.0)
    sigs = {
        "HOT": _sig("HOT", roc=0.9, volume_spike=5.0),
        "OLD": _sig("OLD", roc=-0.2, volume_spike=-0.3),  # cold now
    }
    held = [Position("OLD", qty=100.0, avg_price=9.0)]
    orders = _by_symbol(yolo_propose(sigs, held, 0.0, cfg))
    assert orders["HOT"].action == "buy"
    assert orders["OLD"].action == "sell"
    assert orders["OLD"].target_weight == 0.0


def test_stop_loss_exits_underwater_holding_even_if_still_hot():
    cfg = YoloConfig(top_n=2, max_position_pct=1.0, stop_loss_pct=0.2)
    # GME is still the hottest, but it is down 50% from the avg cost -> force-exit.
    sigs = {
        "GME": _sig("GME", price=10.0, roc=0.9, volume_spike=5.0),
        "AMC": _sig("AMC", price=10.0, roc=0.4, volume_spike=2.0),
    }
    held = [Position("GME", qty=100.0, avg_price=20.0)]  # price 10 <= 20*(1-0.2)=16
    orders = _by_symbol(yolo_propose(sigs, held, 5_000.0, cfg))
    assert orders["GME"].action == "sell"
    assert orders["GME"].reason == "yolo: stop-loss exit"
    assert orders["AMC"].action == "buy"


def test_zero_equity_yields_empty_plan():
    assert yolo_propose({"X": _sig("X", roc=1.0)}, [], 0.0, YoloConfig()).orders == ()


def test_no_hot_names_goes_fully_to_cash():
    cfg = YoloConfig(top_n=3)
    sigs = {"A": _sig("A", roc=-0.1, volume_spike=-0.5),
            "B": _sig("B", roc=-0.2, volume_spike=-0.6)}
    assert yolo_propose(sigs, [], 10_000.0, cfg).orders == ()


def test_regime_overlay_is_ignored_yolo_leans_in():
    cfg = YoloConfig(top_n=1, max_position_pct=1.0)
    sigs = {"HOT": _sig("HOT", roc=0.9, volume_spike=5.0)}
    risk_off = MarketRegime(risk_on=False)
    # Unlike the rebalancer, a risk-off market does not suppress YOLO buys.
    orders = _by_symbol(yolo_propose(sigs, [], 10_000.0, cfg, regime=risk_off))
    assert orders["HOT"].action == "buy"


# -- mode requirements coercion (so --mode yolo "just works") -------------------------
def test_apply_mode_requirements_coerces_yolo_plumbing():
    settings = Settings().model_copy(
        update={"strategy_mode": "yolo", "yolo": YoloConfig(top_n=15, max_position_pct=0.5)})
    assert settings.risk.sizing == "new-dollars"  # not yet coerced
    fixed = apply_mode_requirements(settings)
    assert fixed.risk.sizing == "target-weight"
    assert fixed.engine.screen_bypass is True
    # Per-name cap lifted to the concentration cap; position count lifted to top_n (>10 default).
    assert fixed.risk.max_position_pct >= 0.5
    assert fixed.risk.max_positions >= 15


def test_apply_mode_requirements_noop_for_legacy_modes():
    settings = Settings()  # default rules-only
    assert apply_mode_requirements(settings) is settings
