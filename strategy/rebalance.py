"""Rebalance-mode strategy: a hands-off, config-driven ETF target-weight proposer.

The honest core of the pivot. Instead of hunting alpha, hold a fixed basket of ETFs at
config-driven target weights and trade only when an asset drifts beyond a band (or a
schedule fires). Low turnover is a *feature* — fewer taxable events, less slippage.

This stays a PURE function matching the ``propose_plan`` contract, so the same code runs in
backtest and live (no brain fork). It emits orders expressing the desired *final* weight per
symbol; the sovereign risk gate (with ``RiskParams.sizing="target-weight"``) nets each
against the current holding into a buy or partial-sell delta. The optional regime overlay is
a RISK de-risking control — it only ever *reduces* equity exposure, never predicts direction.

Current prices come from ``SignalSet.price`` (the latest raw close), so the engine/cycle must
compute signals over the full universe ∪ held set for current weights to be valued correctly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.config import RebalanceConfig
from core.contracts import Position, ProposedOrder, ProposedPlan
from signals.signalset import SignalSet
from strategy.regime import MarketRegime


def _current_value(position: Position, signals: Mapping[str, SignalSet]) -> float:
    """Market value of a held position from its qty and latest close (0 if unpriced)."""
    signal = signals.get(position.symbol)
    price = signal.price if signal is not None else None
    if price is None or not (price > 0) or not (position.qty > 0):
        return 0.0
    return position.qty * price


def _derisked_targets(
    config: RebalanceConfig, regime: MarketRegime | None
) -> dict[str, float]:
    """Target weights after the optional regime de-risk overlay.

    When enabled and the market is risk-off, scale every equity-class weight by
    ``(1 - regime_derisk_shift)`` and rotate the freed weight into the defensive symbol (or
    leave it in cash when no defensive symbol is set). Pure risk reduction: it never raises
    a weight except to move freed equity weight into the defensive asset, and never predicts.
    """
    targets = dict(config.target_weights)
    if not config.regime_derisk_enabled or regime is None or regime.risk_on:
        return targets

    shift = config.regime_derisk_shift
    if shift <= 0.0:
        return targets

    freed = 0.0
    for symbol in config.equity_symbols:
        weight = targets.get(symbol)
        if weight is None:
            continue
        reduced = weight * (1.0 - shift)
        freed += weight - reduced
        targets[symbol] = reduced

    if freed > 0.0 and config.defensive_symbol:
        targets[config.defensive_symbol] = targets.get(config.defensive_symbol, 0.0) + freed
    # else: the freed weight simply stays in cash (sum of targets drops).
    return targets


def rebalance_propose(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    config: RebalanceConfig,
    *,
    regime: MarketRegime | None = None,
) -> ProposedPlan:
    """Propose orders that drive the portfolio toward config target weights.

    Trigger:
      - ``"drift"``: act only when some asset's |current - target| exceeds ``drift_band``.
      - ``"schedule"``: always rebalance to target (cadence is the engine's decision interval).
      - ``"both"``: the drift band decides whether to act; when acting, fully rebalance.

    Emits, for the union of target and held symbols, a ``buy`` order carrying the desired
    final weight (the gate derives buy vs partial-sell from the sign of the delta), and a
    full-close ``sell`` for any held symbol whose target is zero (left the universe).
    """
    targets = _derisked_targets(config, regime)

    # Reconstruct equity from cash + marked-to-market holdings (propose_plan is not handed
    # equity; SignalSet.price is the same close the gate's MarketContext uses).
    held_value = sum(_current_value(p, signals) for p in positions)
    equity = cash + held_value
    if equity <= 0.0:
        return ProposedPlan()

    current_value = {p.symbol: _current_value(p, signals) for p in positions}
    symbols = set(targets) | set(current_value)

    # Drift gate: when triggered by drift, only act if some asset has breached the band.
    if config.trigger in ("drift", "both"):
        max_drift = 0.0
        for symbol in symbols:
            current_weight = current_value.get(symbol, 0.0) / equity
            target_weight = targets.get(symbol, 0.0)
            max_drift = max(max_drift, abs(current_weight - target_weight))
        if max_drift <= config.drift_band:
            return ProposedPlan()  # nothing drifted far enough; stay put (low turnover)

    orders: list[ProposedOrder] = []
    for symbol in sorted(symbols):
        target_weight = targets.get(symbol, 0.0)
        if target_weight > 0.0:
            orders.append(
                ProposedOrder(
                    action="buy",
                    symbol=symbol,
                    target_weight=target_weight,
                    conviction=0.0,
                    reason=f"rebalance to {target_weight:.0%}",
                )
            )
        elif symbol in current_value and current_value[symbol] > 0.0:
            # Held but no longer a target -> full close (gate's all-or-nothing sell path).
            orders.append(
                ProposedOrder(
                    action="sell",
                    symbol=symbol,
                    target_weight=0.0,
                    conviction=0.0,
                    reason="rebalance: exit non-target holding",
                )
            )

    return ProposedPlan(orders=tuple(orders))
