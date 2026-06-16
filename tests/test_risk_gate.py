"""Adversarial + property tests for the sovereign risk gate.

The gate must clamp or reject every unsafe input and must *never* return an order set that
violates a hard rule. The property test asserts that invariant over randomized inputs.
"""

from __future__ import annotations

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.contracts import (
    AccountState,
    ExecutablePlan,
    MarketContext,
    Position,
    ProposedOrder,
    ProposedPlan,
)
from risk.gate import _safe_available_cash, apply_risk_gate
from risk.params import RiskParams

EPS = 1e-6


# --------------------------------------------------------------------------------------
# Invariant checker — shared by the property test and reused for sanity in unit tests.
# --------------------------------------------------------------------------------------
def assert_plan_safe(
    gated: ExecutablePlan,
    account: AccountState,
    market: MarketContext,
    params: RiskParams,
) -> None:
    equity = account.equity if math.isfinite(account.equity) else 0.0
    equity = max(0.0, equity)
    available = _safe_available_cash(account)
    loss_breached = (
        math.isfinite(account.day_pnl)
        and math.isfinite(account.equity)
        and account.equity > 0
        and account.day_pnl <= -params.daily_loss_limit * account.equity
    )

    seen_buy: set[str] = set()
    seen_sell: set[str] = set()
    total_buy_dollars = 0.0

    for order in gated.orders:
        # Every order is well-formed and actionable.
        assert order.side in ("buy", "sell")
        assert math.isfinite(order.qty) and order.qty > 0, "non-positive/NaN qty"
        assert math.isfinite(order.est_price) and order.est_price > 0

        if order.side == "buy":
            assert not loss_breached, "buy emitted while daily loss limit breached"
            assert order.symbol not in seen_buy, "duplicate buy"
            seen_buy.add(order.symbol)

            # Known, on-floor, liquid symbol.
            assert order.symbol in market.prices
            assert market.prices[order.symbol] >= params.min_price - EPS
            assert market.avg_dollar_volume.get(order.symbol, 0.0) >= (
                params.min_avg_dollar_volume - EPS
            )

            dollars = order.qty * order.est_price
            # Per-symbol cap.
            assert dollars <= params.max_position_pct * equity * (1 + EPS) + EPS
            total_buy_dollars += dollars
        else:
            assert order.symbol not in seen_sell, "duplicate sell"
            seen_sell.add(order.symbol)

    # Aggregate cash cap.
    assert total_buy_dollars <= available * (1 + EPS) + EPS, "basket overspends cash"

    # Position-count cap (pre-existing holdings above cap are grandfathered, not added to).
    final = set(account.position_symbols())
    for order in gated.orders:
        if order.side == "sell":
            final.discard(order.symbol)
        else:
            final.add(order.symbol)
    assert len(final) <= max(params.max_positions, len(account.position_symbols()))


# --------------------------------------------------------------------------------------
# Adversarial unit tests
# --------------------------------------------------------------------------------------
def test_spend_10x_cash_is_scaled_to_available(account, market, params):
    # Ask for 99% weight; cap clamps to 20% (=$2000) which is within $10k cash.
    plan = ProposedPlan(orders=(ProposedOrder("buy", "AAA", target_weight=10.0),))
    gated = apply_risk_gate(plan, account, market, params)
    assert len(gated.orders) == 1
    cost = gated.orders[0].qty * gated.orders[0].est_price
    assert cost <= params.max_position_pct * account.equity + EPS
    assert cost <= _safe_available_cash(account) + EPS
    assert_plan_safe(gated, account, market, params)


def test_100pct_one_name_is_clamped_to_cap(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("buy", "AAA", target_weight=1.0),))
    gated = apply_risk_gate(plan, account, market, params)
    cost = gated.orders[0].qty * gated.orders[0].est_price
    assert abs(cost - params.max_position_pct * account.equity) < 1e-3


def test_negative_weight_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("buy", "AAA", target_weight=-0.5),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert len(gated.rejected) == 1


def test_unknown_symbol_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("buy", "NOPE", target_weight=0.1),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert "price" in gated.rejected[0][1]


def test_nan_weight_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("buy", "AAA", target_weight=float("nan")),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()


def test_inf_weight_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("buy", "AAA", target_weight=float("inf")),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()


def test_daily_loss_limit_blocks_buys_allows_sells(market, params):
    account = AccountState(
        cash=10_000.0, equity=10_000.0, buying_power=10_000.0,
        positions=(Position("HELD", qty=10.0, avg_price=50.0),),
        day_pnl=-600.0,  # -6% > 5% limit
    )
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "AAA", target_weight=0.1),
        ProposedOrder("sell", "HELD"),
    ))
    gated = apply_risk_gate(plan, account, market, params)
    sides = {o.side for o in gated.orders}
    assert sides == {"sell"}
    assert any("loss limit" in r for _, r in gated.rejected)
    assert_plan_safe(gated, account, market, params)


def test_below_min_price_rejected(account, params):
    market = MarketContext(prices={"PENNY": 1.0}, avg_dollar_volume={"PENNY": 5e9})
    plan = ProposedPlan(orders=(ProposedOrder("buy", "PENNY", target_weight=0.1),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert "min price" in gated.rejected[0][1]


def test_illiquid_rejected(account, params):
    market = MarketContext(prices={"THIN": 100.0}, avg_dollar_volume={"THIN": 1000.0})
    plan = ProposedPlan(orders=(ProposedOrder("buy", "THIN", target_weight=0.1),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert "liquidity" in gated.rejected[0][1]


def test_unknown_action_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("yolo", "AAA", target_weight=0.1),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert "unknown action" in gated.rejected[0][1]


def test_position_count_cap(account, market, params):
    # max_positions=3, one already held (HELD) -> only 2 new buys fit.
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "AAA", target_weight=0.05),
        ProposedOrder("buy", "BBB", target_weight=0.05),
        ProposedOrder("buy", "CCC", target_weight=0.05),
    ))
    gated = apply_risk_gate(plan, account, market, params)
    assert len(gated.orders) == 2
    assert any("count cap" in r for _, r in gated.rejected)
    assert_plan_safe(gated, account, market, params)


def test_sell_with_no_position_rejected(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("sell", "AAA"),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert "no position" in gated.rejected[0][1]


def test_sell_closes_held_quantity(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("sell", "HELD"),))
    gated = apply_risk_gate(plan, account, market, params)
    assert len(gated.orders) == 1
    assert gated.orders[0].side == "sell"
    assert gated.orders[0].qty == 10.0


def test_cash_scaling_across_multiple_buys(market, params):
    # Small account so two 20% buys ($... ) exceed available cash and get scaled down.
    account = AccountState(cash=1_000.0, equity=1_000.0, buying_power=300.0, positions=())
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "AAA", target_weight=0.2),
        ProposedOrder("buy", "BBB", target_weight=0.2),
    ))
    gated = apply_risk_gate(plan, account, market, params)
    total = sum(o.qty * o.est_price for o in gated.orders)
    assert total <= _safe_available_cash(account) + EPS
    assert_plan_safe(gated, account, market, params)


def test_duplicate_buy_rejected(account, market, params):
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "AAA", target_weight=0.05),
        ProposedOrder("buy", "AAA", target_weight=0.05),
    ))
    gated = apply_risk_gate(plan, account, market, params)
    assert len([o for o in gated.orders if o.symbol == "AAA"]) == 1
    assert any("duplicate" in r for _, r in gated.rejected)


def test_hold_produces_no_order(account, market, params):
    plan = ProposedPlan(orders=(ProposedOrder("hold", "AAA"),))
    gated = apply_risk_gate(plan, account, market, params)
    assert gated.orders == ()
    assert gated.rejected == ()


# --------------------------------------------------------------------------------------
# Property test: no input ever yields a rule-violating order set.
# --------------------------------------------------------------------------------------
UNIVERSE = ["AAA", "BBB", "CCC", "DDD", "EEE"]

_weights = st.one_of(
    st.floats(min_value=-2.0, max_value=5.0),
    st.sampled_from([float("nan"), float("inf"), float("-inf"), 0.0]),
)
_orders = st.builds(
    ProposedOrder,
    action=st.sampled_from(["buy", "sell", "hold", "garbage"]),
    symbol=st.sampled_from(UNIVERSE + ["UNKNOWN"]),
    target_weight=_weights,
    conviction=st.floats(min_value=0.0, max_value=1.0),
    reason=st.just(""),
)


@st.composite
def _market(draw) -> MarketContext:
    prices = {s: draw(st.floats(min_value=0.5, max_value=500.0)) for s in UNIVERSE}
    adv = {s: draw(st.floats(min_value=0.0, max_value=1e10)) for s in UNIVERSE}
    return MarketContext(prices=prices, avg_dollar_volume=adv)


@st.composite
def _account(draw) -> AccountState:
    held = draw(st.lists(st.sampled_from(UNIVERSE), max_size=4, unique=True))
    positions = tuple(
        Position(s, qty=draw(st.floats(min_value=0.0, max_value=20.0)), avg_price=50.0)
        for s in held
    )
    cash = draw(st.floats(min_value=0.0, max_value=100_000.0))
    return AccountState(
        cash=cash,
        equity=draw(st.floats(min_value=0.0, max_value=100_000.0)),
        buying_power=draw(st.floats(min_value=0.0, max_value=100_000.0)),
        positions=positions,
        day_pnl=draw(st.floats(min_value=-50_000.0, max_value=50_000.0)),
    )


@st.composite
def _params(draw) -> RiskParams:
    return RiskParams(
        max_position_pct=draw(st.floats(min_value=0.01, max_value=1.0)),
        max_positions=draw(st.integers(min_value=1, max_value=6)),
        min_price=draw(st.floats(min_value=0.0, max_value=50.0)),
        min_avg_dollar_volume=draw(st.floats(min_value=0.0, max_value=5e9)),
        daily_loss_limit=draw(st.floats(min_value=0.01, max_value=1.0)),
    )


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(
    orders=st.lists(_orders, max_size=8),
    market=_market(),
    account=_account(),
    params=_params(),
)
def test_property_gate_never_violates_rules(orders, market, account, params):
    gated = apply_risk_gate(ProposedPlan(orders=tuple(orders)), account, market, params)
    assert_plan_safe(gated, account, market, params)
