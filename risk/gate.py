"""The sovereign risk gate: deterministic, pure, non-LLM.

``apply_risk_gate`` takes a (possibly adversarial) ``ProposedPlan`` plus account + market
state and returns an ``ExecutablePlan`` whose order set, *by construction*, violates none
of the hard rules. Anything it cannot make safe is dropped into the plan's ``rejected``
audit trail with a reason — it never raises, never guesses, never lets unsafe through.

Hard rules (all config-driven via ``RiskParams``):
  1. Malformed input (bad action/symbol, NaN/inf/non-positive weight) is rejected.
  2. Daily loss limit breached -> only sells/holds allowed (no new risk).
  3. Per-symbol cap: a buy's weight is clamped to ``max_position_pct`` of equity.
  4. Price/liquidity floor: buys into sub-floor or illiquid names are rejected.
  5. Position-count cap: total concurrent positions never exceeds ``max_positions``.
  6. Cash safety: aggregate buy cost never exceeds available cash/buying power
     (the basket is scaled down proportionally if needed).

Sizing simplifications (intentional for this slice, documented):
  - A ``sell`` fully closes the held position for that symbol (target_weight ignored);
    a sell with no held position is rejected. Exits are always permitted (they reduce
    risk) regardless of price/liquidity floors.
  - A ``buy``'s ``target_weight`` sizes *new* dollars to deploy; net-of-existing-holding
    rebalancing is a later refinement.
"""

from __future__ import annotations

from core.contracts import (
    AccountState,
    ExecutableOrder,
    ExecutablePlan,
    MarketContext,
    ProposedOrder,
    ProposedPlan,
    _is_finite_number,
)
from risk.params import RiskParams


def apply_risk_gate(
    plan: ProposedPlan,
    account: AccountState,
    market: MarketContext,
    params: RiskParams,
) -> ExecutablePlan:
    """Gate a proposed plan into a provably-safe executable plan.

    Two sizing modes (``params.sizing``), both running the same sovereign hard rules:
      - ``"new-dollars"`` (default): a buy's ``target_weight`` sizes *new* dollars to deploy
        (the legacy alpha path). A sell fully closes the held position.
      - ``"target-weight"``: a buy's ``target_weight`` is the desired *final* fraction of
        equity; the gate nets it against the current holding into a buy (delta>0) or a
        partial sell (delta<0), applies the min-trade and max-turnover churn guards, and
        emits sells before buys so proceeds fund the buys at the next open.
    """
    if params.sizing == "target-weight":
        return _gate_target_weight(plan, account, market, params)

    approved: list[ExecutableOrder] = []
    rejected: list[tuple[ProposedOrder, str]] = []

    # Available spendable cash: the conservative minimum of settled cash and buying power,
    # floored at zero and guarded against non-finite inputs.
    available_cash = _safe_available_cash(account)
    equity = account.cash if not _is_finite_number(account.equity) else float(account.equity)
    equity = max(0.0, equity) if _is_finite_number(equity) else 0.0

    loss_limit_breached = _daily_loss_breached(account, params)

    held_symbols = account.position_symbols()
    held_qty = {p.symbol: p.qty for p in account.positions}

    # Symbols projected to be open after this gate runs; seeds the position-count cap.
    projected_symbols = set(held_symbols)
    seen_sell: set[str] = set()
    seen_buy: set[str] = set()

    # Buys are collected first (validated + clamped), then sized together so the cash cap
    # can scale the whole basket proportionally. Sells/holds are resolved inline.
    pending_buys: list[tuple[ProposedOrder, float]] = []  # (order, desired_dollars)

    for order in plan.orders:
        action = order.action
        symbol = order.symbol

        if not isinstance(symbol, str) or not symbol:
            rejected.append((order, "invalid symbol"))
            continue

        if action == "hold":
            continue  # valid no-op; produces no order

        if action == "sell":
            if symbol in seen_sell:
                rejected.append((order, "duplicate sell for symbol"))
                continue
            seen_sell.add(symbol)
            qty = held_qty.get(symbol)
            if qty is None or not _is_finite_number(qty) or qty <= 0:
                rejected.append((order, "no position to sell"))
                continue
            price = _exit_price(symbol, market, account)
            if price is None:
                rejected.append((order, "no price available to value sell"))
                continue
            approved.append(ExecutableOrder(symbol=symbol, side="sell", qty=float(qty),
                                            est_price=price))
            projected_symbols.discard(symbol)  # full close frees a position slot
            continue

        if action != "buy":
            rejected.append((order, f"unknown action: {action!r}"))
            continue

        # --- buy validation ---
        if loss_limit_breached:
            rejected.append((order, "daily loss limit breached: no new buys"))
            continue
        if symbol in seen_buy:
            rejected.append((order, "duplicate buy for symbol"))
            continue
        seen_buy.add(symbol)

        if not _is_finite_number(order.target_weight) or order.target_weight <= 0:
            rejected.append((order, "non-positive or non-finite target_weight"))
            continue

        price = market.prices.get(symbol)
        if price is None or not _is_finite_number(price) or price <= 0:
            rejected.append((order, "unknown or invalid price"))
            continue
        if price < params.min_price:
            rejected.append((order, "below min price floor"))
            continue
        adv = market.avg_dollar_volume.get(symbol, 0.0)
        if not _is_finite_number(adv) or adv < params.min_avg_dollar_volume:
            rejected.append((order, "below liquidity floor"))
            continue

        # Position-count cap: a buy into a new symbol must fit under max_positions.
        if symbol not in projected_symbols:
            if len(projected_symbols) >= params.max_positions:
                rejected.append((order, "position count cap reached"))
                continue
            projected_symbols.add(symbol)

        # Per-symbol cap: clamp weight to max_position_pct of equity.
        weight = min(float(order.target_weight), params.max_position_pct)
        desired_dollars = weight * equity
        if desired_dollars <= 0:
            rejected.append((order, "zero sizing (no equity)"))
            continue
        pending_buys.append((order, desired_dollars))

    # --- cash cap: scale the whole buy basket down if it would overspend ---
    total_desired = sum(d for _, d in pending_buys)
    scale = 1.0
    if total_desired > available_cash and total_desired > 0:
        scale = available_cash / total_desired

    for order, desired_dollars in pending_buys:
        dollars = desired_dollars * scale
        price = market.prices[order.symbol]  # validated above
        qty = dollars / price
        if not _is_finite_number(qty) or qty <= 0:
            rejected.append((order, "insufficient cash to size order"))
            continue
        approved.append(ExecutableOrder(symbol=order.symbol, side="buy", qty=qty,
                                        est_price=float(price)))

    return ExecutablePlan(orders=tuple(approved), rejected=tuple(rejected))


def _gate_target_weight(
    plan: ProposedPlan,
    account: AccountState,
    market: MarketContext,
    params: RiskParams,
) -> ExecutablePlan:
    """Rebalance-to-target sizing: net each buy order's final weight against the holding.

    Sells (full closes and drift-reducing partial sells) are emitted *before* buys so the
    simulated/live broker frees their proceeds for the buys at the next open. All the hard
    rules of the legacy path still apply to buys (price/liquidity floors, per-symbol cap,
    position-count cap, daily-loss-limit, cash cap); partial sells are always permitted as
    risk-reducing exits. Two churn guards: a min-trade floor on the rebalancing deltas and a
    max-turnover cap that scales every leg down proportionally.
    """
    sells: list[ExecutableOrder] = []
    rejected: list[tuple[ProposedOrder, str]] = []

    available_cash = _safe_available_cash(account)
    equity = account.cash if not _is_finite_number(account.equity) else float(account.equity)
    equity = max(0.0, equity) if _is_finite_number(equity) else 0.0

    loss_limit_breached = _daily_loss_breached(account, params)
    held_qty = {p.symbol: p.qty for p in account.positions}

    # Min-trade floor on the rebalancing delta (in dollars). Full-close exits below are NOT
    # suppressed (we still want non-target holdings gone); only delta legs are guarded.
    min_trade = max(params.min_trade_dollars, params.min_trade_pct * equity)

    projected_symbols = set(account.position_symbols())
    seen_sell: set[str] = set()
    seen_buy: set[str] = set()

    sell_proceeds = 0.0
    pending_buys: list[tuple[ProposedOrder, float, float]] = []  # (order, desired_dollars, price)

    for order in plan.orders:
        action = order.action
        symbol = order.symbol

        if not isinstance(symbol, str) or not symbol:
            rejected.append((order, "invalid symbol"))
            continue
        if action == "hold":
            continue

        if action == "sell":  # explicit full close of a non-target holding
            if symbol in seen_sell:
                rejected.append((order, "duplicate sell for symbol"))
                continue
            seen_sell.add(symbol)
            qty = held_qty.get(symbol)
            if qty is None or not _is_finite_number(qty) or qty <= 0:
                rejected.append((order, "no position to sell"))
                continue
            price = _exit_price(symbol, market, account)
            if price is None:
                rejected.append((order, "no price available to value sell"))
                continue
            sells.append(ExecutableOrder(symbol=symbol, side="sell", qty=float(qty),
                                         est_price=price))
            sell_proceeds += float(qty) * price
            projected_symbols.discard(symbol)
            continue

        if action != "buy":
            rejected.append((order, f"unknown action: {action!r}"))
            continue

        # --- target-weight buy order: derive the buy/partial-sell delta ---
        if symbol in seen_buy:
            rejected.append((order, "duplicate buy for symbol"))
            continue
        seen_buy.add(symbol)
        if not _is_finite_number(order.target_weight) or order.target_weight <= 0:
            rejected.append((order, "non-positive or non-finite target_weight"))
            continue
        price = market.prices.get(symbol)
        if price is None or not _is_finite_number(price) or price <= 0:
            rejected.append((order, "unknown or invalid price"))
            continue

        weight = min(float(order.target_weight), params.max_position_pct)
        current_value = held_qty.get(symbol, 0.0) * price
        delta = weight * equity - current_value

        if abs(delta) <= min_trade:
            rejected.append((order, "below min-trade size"))
            continue

        if delta < 0:  # over target -> partial sell (always permitted; risk-reducing)
            qty = (-delta) / price
            if not _is_finite_number(qty) or qty <= 0:
                rejected.append((order, "invalid partial-sell sizing"))
                continue
            sells.append(ExecutableOrder(symbol=symbol, side="sell", qty=qty, est_price=price))
            sell_proceeds += qty * price
            continue

        # delta > 0 -> buy up to target, under the same hard rules as the legacy path.
        if loss_limit_breached:
            rejected.append((order, "daily loss limit breached: no new buys"))
            continue
        if price < params.min_price:
            rejected.append((order, "below min price floor"))
            continue
        adv = market.avg_dollar_volume.get(symbol, 0.0)
        if not _is_finite_number(adv) or adv < params.min_avg_dollar_volume:
            rejected.append((order, "below liquidity floor"))
            continue
        if symbol not in projected_symbols:
            if len(projected_symbols) >= params.max_positions:
                rejected.append((order, "position count cap reached"))
                continue
            projected_symbols.add(symbol)
        pending_buys.append((order, delta, price))

    # Cash cap: buys are funded by available cash plus this rebalance's sell proceeds (the
    # sells fill first at the same open). Scale the buy basket down if it would overspend.
    available_for_buys = available_cash + sell_proceeds
    total_desired = sum(d for _, d, _ in pending_buys)
    buy_scale = 1.0
    if total_desired > available_for_buys and total_desired > 0:
        buy_scale = available_for_buys / total_desired

    buys: list[ExecutableOrder] = []
    for order, desired_dollars, price in pending_buys:
        qty = desired_dollars * buy_scale / price
        if not _is_finite_number(qty) or qty <= 0:
            rejected.append((order, "insufficient cash to size order"))
            continue
        buys.append(ExecutableOrder(symbol=order.symbol, side="buy", qty=qty,
                                    est_price=float(price)))

    # Turnover guard: cap total traded notional at max_turnover_pct of equity, scaling every
    # sized leg (sells + buys) down proportionally. Deterministic; None = no cap.
    legs = sells + buys
    if params.max_turnover_pct is not None:
        total_notional = sum(o.qty * o.est_price for o in legs)
        turnover_cap = params.max_turnover_pct * equity
        if total_notional > turnover_cap and total_notional > 0:
            turnover_scale = turnover_cap / total_notional
            legs = [ExecutableOrder(symbol=o.symbol, side=o.side, qty=o.qty * turnover_scale,
                                    est_price=o.est_price) for o in legs]

    return ExecutablePlan(orders=tuple(legs), rejected=tuple(rejected))


def _safe_available_cash(account: AccountState) -> float:
    """Conservative spendable cash: min(cash, buying_power), floored at 0."""
    cash = float(account.cash) if _is_finite_number(account.cash) else 0.0
    bp = float(account.buying_power) if _is_finite_number(account.buying_power) else 0.0
    return max(0.0, min(cash, bp))


def _daily_loss_breached(account: AccountState, params: RiskParams) -> bool:
    """True if the session loss has exceeded the configured fraction of equity."""
    if not _is_finite_number(account.day_pnl) or not _is_finite_number(account.equity):
        return False
    equity = float(account.equity)
    if equity <= 0:
        return False
    return float(account.day_pnl) <= -params.daily_loss_limit * equity


def _exit_price(symbol: str, market: MarketContext, account: AccountState) -> float | None:
    """Price to value a sell: live market price if known, else the position's avg price."""
    price = market.prices.get(symbol)
    if price is not None and _is_finite_number(price) and price > 0:
        return float(price)
    for p in account.positions:
        if p.symbol == symbol and _is_finite_number(p.avg_price) and p.avg_price > 0:
            return float(p.avg_price)
    return None
