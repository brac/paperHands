"""Tests for the dual-mode strategy — rules determinism, news doctrine, llm parsing, guard.

All offline: llm mode uses a stub LLMClient; rules mode is pure. No network anywhere.
"""

from __future__ import annotations

import json

import pytest

from core.config import StrategyConfig
from core.contracts import Position, ProposedOrder, ProposedPlan
from signals.signalset import SignalSet
from strategy import StrategyContext, propose_plan
from strategy.guard import enforce_technicals_primary, has_technical_support
from strategy.llm import build_prompt, parse_plan

_CFG = StrategyConfig()


def _sig(symbol: str, **overrides) -> SignalSet:
    return SignalSet(symbol=symbol, **overrides)


def _ctx(mode: str = "rules-only", config: StrategyConfig = _CFG, client=None) -> StrategyContext:
    return StrategyContext(mode=mode, config=config, llm_client=client)  # type: ignore[arg-type]


# Common fixtures.
_MOM = _sig("MOM", roc=0.10, trend_strength=0.05, rsi=50.0, zscore=0.0)   # momentum buy
_MR = _sig("MR", roc=-0.05, trend_strength=-0.02, zscore=-2.0)            # mean-reversion buy
_FLAT = _sig("FLAT", roc=0.0, trend_strength=0.0, zscore=0.0)            # no support


# --------------------------------------------------------------------------------------
# Rules mode
# --------------------------------------------------------------------------------------
def test_momentum_buy_conviction_weighted():
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx())
    assert len(plan.orders) == 1
    o = plan.orders[0]
    assert o.action == "buy" and o.symbol == "MOM"
    assert o.conviction == pytest.approx(0.5)              # 0.10 / 0.20
    assert o.target_weight == pytest.approx(0.5 * _CFG.max_target_weight)


def test_mean_reversion_buy():
    plan = propose_plan({"MR": _MR}, [], 10_000.0, _ctx())
    assert [o.symbol for o in plan.orders] == ["MR"]
    assert plan.orders[0].conviction == pytest.approx(2.0 / 3.0)  # -zscore / 3.0


def test_no_technical_support_no_buy():
    assert propose_plan({"FLAT": _FLAT}, [], 10_000.0, _ctx()).orders == ()


def test_rsi_overbought_suppresses_momentum_buy():
    hot = _sig("HOT", roc=0.10, trend_strength=0.05, rsi=80.0)
    assert propose_plan({"HOT": hot}, [], 10_000.0, _ctx()).orders == ()


def test_no_cash_no_buys():
    assert propose_plan({"MOM": _MOM}, [], 0.0, _ctx()).orders == ()


# --------------------------------------------------------------------------------------
# Phase-3 conviction levers (default off -> no-op)
# --------------------------------------------------------------------------------------
def test_max_atr_pct_filters_volatile_buy():
    vol = _sig("VOL", roc=0.10, trend_strength=0.05, rsi=50.0, atr_pct=0.10)
    # Default config has no vol cap -> the buy goes through.
    assert [o.symbol for o in propose_plan({"VOL": vol}, [], 10_000.0, _ctx()).orders] == ["VOL"]
    # With a 5% cap, the 10%-ATR name is filtered out.
    capped = _ctx(config=StrategyConfig(max_atr_pct=0.05))
    assert propose_plan({"VOL": vol}, [], 10_000.0, capped).orders == ()


def test_high_proximity_weight_downweights_far_from_high():
    far = _sig("FAR", roc=0.10, trend_strength=0.05, rsi=50.0, dist_from_high=-0.5)
    # Off by default: dist_from_high is ignored, conviction stays 0.5 (0.10 / 0.20).
    off = propose_plan({"FAR": far}, [], 10_000.0, _ctx()).orders[0]
    assert off.conviction == pytest.approx(0.5)
    # Full weight: conviction *= proximity = 0.5 * (1 + (-0.5)) = 0.5 * 0.5 = 0.25.
    weighted = _ctx(config=StrategyConfig(high_proximity_weight=1.0))
    o = propose_plan({"FAR": far}, [], 10_000.0, weighted).orders[0]
    assert o.conviction == pytest.approx(0.25)


def test_max_new_positions_caps_buys_by_conviction():
    signals = {
        "M1": _sig("M1", roc=0.20, trend_strength=0.05),  # conviction 1.0
        "M2": _sig("M2", roc=0.10, trend_strength=0.05),  # 0.5
        "M3": _sig("M3", roc=0.05, trend_strength=0.05),  # 0.25
    }
    plan = propose_plan(signals, [], 10_000.0, _ctx(config=StrategyConfig(max_new_positions=2)))
    assert [o.symbol for o in plan.orders] == ["M1", "M2"]


def test_sell_bearish_held_name():
    bearish = _sig("OLD", roc=-0.05, trend_strength=-0.02, zscore=0.0)
    plan = propose_plan({"OLD": bearish}, [Position("OLD", 10.0, 90.0)], 10_000.0, _ctx())
    assert [(o.action, o.symbol) for o in plan.orders] == [("sell", "OLD")]


def test_regime_filter_drops_buys_but_keeps_sells():
    from strategy.regime import MarketRegime

    on = _ctx(config=StrategyConfig(regime_filter_enabled=True))
    risk_off = MarketRegime(risk_on=False)
    # A momentum buy is suppressed when the market is risk-off and the filter is on...
    assert propose_plan({"MOM": _MOM}, [], 10_000.0, on, regime=risk_off).orders == ()
    # ...while the default (no regime passed) is unchanged.
    assert [o.symbol for o in propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx()).orders] == ["MOM"]


def test_held_without_signal_is_left_alone():
    assert propose_plan({}, [Position("GONE", 5.0, 10.0)], 10_000.0, _ctx()).orders == ()


def test_cross_sectional_gate_keeps_top_fraction_by_roc():
    signals = {
        "M1": _sig("M1", roc=0.20, trend_strength=0.05),
        "M2": _sig("M2", roc=0.10, trend_strength=0.05),
        "M3": _sig("M3", roc=0.05, trend_strength=0.05),
    }
    # fraction 0.5 of 3 candidates -> keep top 2 by roc; M3 (lowest roc) is dropped.
    gated = _ctx(config=StrategyConfig(momentum_rank_fraction=0.5))
    assert {o.symbol for o in propose_plan(signals, [], 10_000.0, gated).orders} == {"M1", "M2"}
    # Default (1.0) keeps all three.
    assert len(propose_plan(signals, [], 10_000.0, _ctx()).orders) == 3


def test_stop_loss_sells_held_name_past_threshold():
    # Flat signal (no buy support, not bearish) but price 80 vs entry 100 = -20%.
    held = _sig("HELD", roc=0.0, trend_strength=0.0, zscore=0.0, price=80.0)
    pos = [Position("HELD", 10.0, 100.0)]
    stopped = _ctx(config=StrategyConfig(stop_loss_pct=0.15))
    plan = propose_plan({"HELD": held}, pos, 10_000.0, stopped)
    assert [(o.action, o.symbol) for o in plan.orders] == [("sell", "HELD")]
    assert plan.orders[0].reason == "stop loss"
    # Off by default: a non-bearish held name is simply held.
    assert propose_plan({"HELD": held}, pos, 10_000.0, _ctx()).orders == ()


def test_determinism():
    signals = {"MOM": _MOM, "MR": _MR}
    a = propose_plan(signals, [], 10_000.0, _ctx())
    b = propose_plan(signals, [], 10_000.0, _ctx())
    assert a == b


# --------------------------------------------------------------------------------------
# News doctrine (secondary; never originates)
# --------------------------------------------------------------------------------------
def test_news_boosts_conviction():
    boosted = _sig("MOM", roc=0.10, trend_strength=0.05, recent_8k=True)
    plan = propose_plan({"MOM": boosted}, [], 10_000.0, _ctx())
    assert plan.orders[0].conviction == pytest.approx(0.5 + _CFG.news_conviction_boost)


def test_news_veto_drops_buy():
    vetoed = _sig("MOM", roc=0.10, trend_strength=0.05, news_sentiment=-0.6)
    assert propose_plan({"MOM": vetoed}, [], 10_000.0, _ctx()).orders == ()


def test_news_alone_never_originates_a_buy():
    # Strong positive news but zero technical support -> still no buy.
    news_only = _sig("FLAT", roc=0.0, trend_strength=0.0, recent_8k=True, news_sentiment=0.9)
    assert propose_plan({"FLAT": news_only}, [], 10_000.0, _ctx()).orders == ()


# --------------------------------------------------------------------------------------
# Technicals-primary guard (applies in both modes)
# --------------------------------------------------------------------------------------
def test_guard_drops_unsupported_buy_keeps_rest():
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "MOM", target_weight=0.1),     # supported -> kept
        ProposedOrder("buy", "FLAT", target_weight=0.1),    # unsupported -> dropped
        ProposedOrder("buy", "UNKNOWN", target_weight=0.1),  # no signal -> dropped
        ProposedOrder("sell", "X"),
        ProposedOrder("hold", "Y"),
    ))
    out = enforce_technicals_primary(plan, {"MOM": _MOM, "FLAT": _FLAT}, _CFG)
    assert [(o.action, o.symbol) for o in out.orders] == [
        ("buy", "MOM"), ("sell", "X"), ("hold", "Y"),
    ]


def test_has_technical_support_predicate():
    assert has_technical_support(_MOM, _CFG) is True
    assert has_technical_support(_MR, _CFG) is True
    assert has_technical_support(_FLAT, _CFG) is False


# --------------------------------------------------------------------------------------
# LLM mode (stub client)
# --------------------------------------------------------------------------------------
class _StubClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


class _RaisingClient:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("boom")


def test_llm_valid_json_array_parsed_and_guarded():
    resp = json.dumps([
        {"action": "buy", "symbol": "MOM", "target_weight": 0.1, "conviction": 0.8, "reason": "up"},
    ])
    client = _StubClient(resp)
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=client))
    assert [(o.action, o.symbol) for o in plan.orders] == [("buy", "MOM")]
    # The prompt carried the doctrine + the serialized signal.
    system, user = client.calls[0]
    assert "PRIMARY" in system and "MOM" in user


def test_llm_strips_code_fences():
    resp = "```json\n[{\"action\": \"buy\", \"symbol\": \"MOM\", \"target_weight\": 0.1}]\n```"
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=_StubClient(resp)))
    assert [o.symbol for o in plan.orders] == ["MOM"]


def test_llm_accepts_orders_object_form():
    resp = json.dumps({"orders": [{"action": "buy", "symbol": "MOM", "target_weight": 0.1}]})
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=_StubClient(resp)))
    assert [o.symbol for o in plan.orders] == ["MOM"]


def test_llm_malformed_output_is_safe_empty_plan():
    client = _StubClient("not json {{{")
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=client))
    assert plan.orders == ()


def test_llm_news_only_buy_dropped_by_guard():
    # The model invents a buy for an unsupported name -> guard removes it.
    resp = json.dumps([{"action": "buy", "symbol": "FLAT", "target_weight": 0.2}])
    plan = propose_plan({"FLAT": _FLAT}, [], 10_000.0, _ctx("llm", client=_StubClient(resp)))
    assert plan.orders == ()


def test_llm_client_exception_is_safe_empty_plan():
    plan = propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=_RaisingClient()))
    assert plan.orders == ()


def test_llm_mode_without_client_is_safe_empty_plan():
    assert propose_plan({"MOM": _MOM}, [], 10_000.0, _ctx("llm", client=None)).orders == ()


# --------------------------------------------------------------------------------------
# parse_plan robustness
# --------------------------------------------------------------------------------------
def test_parse_plan_skips_invalid_items_and_coerces():
    raw = json.dumps([
        {"action": "buy", "symbol": "AAA", "target_weight": 0.1},
        {"action": "teleport", "symbol": "BBB"},          # bad action -> skipped
        {"action": "buy"},                                  # missing symbol -> skipped
        {"action": "buy", "symbol": "CCC", "target_weight": "oops"},  # bad weight -> 0.0
    ])
    plan = parse_plan(raw)
    assert [(o.symbol, o.target_weight) for o in plan.orders] == [("AAA", 0.1), ("CCC", 0.0)]


def test_build_prompt_states_doctrine():
    system, user = build_prompt({"MOM": _MOM}, [Position("MOM", 1.0, 100.0)], 5_000.0, _CFG)
    assert "SECONDARY" in system and "JSON" in system
    assert "MOM" in user and "5000" in user.replace(".0", "")
