"""Tests for the gate's target-weight (rebalance) sizing mode.

The sovereign gate stays the single authority: in ``sizing="target-weight"`` it nets each
buy order's final weight against the holding into a buy or partial sell, applies the
min-trade and max-turnover churn guards, and still enforces every hard rule. The legacy
``new-dollars`` path is covered by ``test_risk_gate.py``; here we exercise the new branch.
"""

from __future__ import annotations

import pytest

from core.contracts import (
    AccountState,
    MarketContext,
    Position,
    ProposedOrder,
    ProposedPlan,
)
from risk.gate import apply_risk_gate
from risk.params import RiskParams

_PARAMS = RiskParams(sizing="target-weight", max_position_pct=1.0, max_positions=10,
                     min_avg_dollar_volume=0.0, min_price=0.0)
_MARKET = MarketContext(
    prices={"SPY": 100.0, "BND": 100.0, "GLD": 100.0},
    avg_dollar_volume={"SPY": 1e9, "BND": 1e9, "GLD": 1e9},
)


def _account(cash: float, positions: tuple[Position, ...] = ()) -> AccountState:
    held = sum(p.qty * 100.0 for p in positions)  # all test prices are 100
    equity = cash + held
    return AccountState(cash=cash, equity=equity, buying_power=cash, positions=positions)


def _buy(symbol: str, weight: float) -> ProposedOrder:
    return ProposedOrder(action="buy", symbol=symbol, target_weight=weight)


def _by_symbol(plan):
    return {o.symbol: o for o in plan.orders}


def test_under_target_buys_only_the_delta():
    # Hold 30% SPY, target 60% -> buy the 30% delta, not a fresh 60%.
    account = _account(7_000.0, (Position("SPY", qty=30.0, avg_price=100.0),))  # equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.6),))
    gated = apply_risk_gate(plan, account, _MARKET, _PARAMS)
    spy = _by_symbol(gated)["SPY"]
    assert spy.side == "buy"
    assert spy.qty * spy.est_price == pytest.approx(3_000.0)  # 0.6*10k - 3k current


def test_over_target_partially_sells_down():
    # Hold 80% SPY, target 60% -> partial SELL of 20%, not a full close.
    account = _account(2_000.0, (Position("SPY", qty=80.0, avg_price=100.0),))  # equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.6),))
    gated = apply_risk_gate(plan, account, _MARKET, _PARAMS)
    spy = _by_symbol(gated)["SPY"]
    assert spy.side == "sell"
    assert spy.qty == pytest.approx(20.0)  # reduce 80 -> 60 shares (20% of 10k @ 100)


def test_sells_emitted_before_buys_so_proceeds_fund_buys():
    # Fully invested: 100% BND, want 60% SPY / 40% BND. Buy must be funded by the BND sell.
    account = _account(0.0, (Position("BND", qty=100.0, avg_price=100.0),))  # equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.6), _buy("BND", 0.4)))
    gated = apply_risk_gate(plan, account, _MARKET, _PARAMS)
    sides = [o.side for o in gated.orders]
    assert sides[0] == "sell" and "buy" in sides  # sells ordered first
    spy = _by_symbol(gated)["SPY"]
    assert spy.side == "buy"
    assert spy.qty * spy.est_price == pytest.approx(6_000.0)  # full 60% sized from proceeds


def test_min_trade_floor_skips_tiny_drift():
    params = _PARAMS.model_copy(update={"min_trade_dollars": 500.0})
    # Hold 58% SPY, target 60% -> $200 delta, below the $500 floor -> skipped.
    account = _account(4_200.0, (Position("SPY", qty=58.0, avg_price=100.0),))  # equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.6),))
    gated = apply_risk_gate(plan, account, _MARKET, params)
    assert gated.orders == ()
    assert any("min-trade" in reason for _, reason in gated.rejected)


def test_max_turnover_scales_all_legs():
    params = _PARAMS.model_copy(update={"max_turnover_pct": 0.05})  # cap 5% of equity = $500
    account = _account(0.0, (Position("BND", qty=100.0, avg_price=100.0),))  # equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.6), _buy("BND", 0.4)))
    gated = apply_risk_gate(plan, account, _MARKET, params)
    total_notional = sum(o.qty * o.est_price for o in gated.orders)
    assert total_notional == pytest.approx(500.0, rel=1e-6)  # scaled to the turnover cap


def test_per_symbol_cap_still_enforced():
    params = _PARAMS.model_copy(update={"max_position_pct": 0.5})
    account = _account(10_000.0)  # all cash, equity 10k
    plan = ProposedPlan(orders=(_buy("SPY", 0.9),))  # asks 90%, cap 50%
    gated = apply_risk_gate(plan, account, _MARKET, params)
    spy = _by_symbol(gated)["SPY"]
    assert spy.qty * spy.est_price == pytest.approx(5_000.0)  # clamped to 50%


def test_daily_loss_limit_blocks_new_buys_but_allows_derisking_sells():
    params = _PARAMS.model_copy(update={"daily_loss_limit": 0.05})
    account = AccountState(
        cash=2_000.0, equity=10_000.0, buying_power=2_000.0,
        positions=(Position("SPY", qty=80.0, avg_price=100.0),),
        day_pnl=-600.0,  # -6% breaches the 5% limit
    )
    # A buy (GLD up to target) is blocked; an over-target SPY still de-risks (partial sell).
    plan = ProposedPlan(orders=(_buy("GLD", 0.1), _buy("SPY", 0.6)))
    gated = apply_risk_gate(plan, account, _MARKET, params)
    by = _by_symbol(gated)
    assert "GLD" not in by  # new buy blocked
    assert by["SPY"].side == "sell"  # de-risking sell still allowed
    assert any("daily loss limit" in reason for _, reason in gated.rejected)


def test_below_min_price_buy_rejected():
    params = _PARAMS.model_copy(update={"min_price": 50.0})
    market = MarketContext(prices={"PNY": 10.0}, avg_dollar_volume={"PNY": 1e9})
    account = _account(10_000.0)
    plan = ProposedPlan(orders=(_buy("PNY", 0.5),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert any("min price" in reason for _, reason in gated.rejected)


def test_explicit_full_close_sell_exits_position():
    account = _account(5_000.0, (Position("OLD", qty=50.0, avg_price=100.0),))
    market = MarketContext(prices={"OLD": 100.0}, avg_dollar_volume={"OLD": 1e9})
    plan = ProposedPlan(orders=(ProposedOrder(action="sell", symbol="OLD", target_weight=0.0),))
    gated = apply_risk_gate(plan, account, market, _PARAMS)
    old = _by_symbol(gated)["OLD"]
    assert old.side == "sell" and old.qty == pytest.approx(50.0)
