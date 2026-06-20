"""propose_plan — the swappable, dual-mode strategy entry point.

The same pure function runs in backtest, paper, and live. It dispatches on the context's
mode, then applies the sovereign-doctrine guard (``enforce_technicals_primary``) so a
news-only or hallucinated buy can never survive, regardless of mode. The risk gate is still
the final authority downstream; this only produces the *proposal*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.contracts import Position, ProposedPlan
from legacy.strategy.guard import enforce_technicals_primary
from legacy.strategy.llm import llm_propose
from legacy.strategy.rules import rules_propose
from signals.signalset import SignalSet
from strategy.context import StrategyContext
from strategy.rebalance import rebalance_propose
from strategy.regime import MarketRegime, enforce_regime
from strategy.yolo import yolo_propose


def propose_plan(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    ctx: StrategyContext,
    *,
    regime: MarketRegime | None = None,
) -> ProposedPlan:
    """Produce a ProposedPlan from signals + account state under the configured mode.

    ``regime`` is an optional market-trend overlay (computed upstream from the reference index);
    when the filter is enabled and the market is risk-off, new buys are dropped.
    """
    # Rebalance mode returns early: it emits target-weight orders the gate sizes as deltas,
    # and does its OWN regime de-risking. The alpha-doctrine guards below
    # (enforce_technicals_primary / enforce_regime) would wrongly delete every ETF order, so
    # they must NOT run for the rebalancer.
    if ctx.mode == "rebalance":
        if ctx.rebalance is None:
            return ProposedPlan()  # misconfigured rebalance mode -> safe empty plan
        return rebalance_propose(
            signals, positions, cash, ctx.rebalance, regime=regime
        )

    # YOLO mode returns early for the same reason as rebalance: it emits target-weight orders the
    # gate sizes as deltas, and the alpha-doctrine guards below would delete every momentum buy.
    # It deliberately ignores the regime de-risk overlay (it leans into momentum, not away).
    if ctx.mode == "yolo":
        if ctx.yolo is None:
            return ProposedPlan()  # misconfigured yolo mode -> safe empty plan
        return yolo_propose(signals, positions, cash, ctx.yolo, regime=regime)

    if ctx.mode == "llm":
        if ctx.llm_client is None:
            plan = ProposedPlan()  # misconfigured llm mode -> safe empty plan
        else:
            plan = llm_propose(signals, positions, cash, ctx.config, ctx.llm_client)
    else:  # rules-only
        plan = rules_propose(signals, positions, cash, ctx.config)

    plan = enforce_technicals_primary(plan, signals, ctx.config)
    return enforce_regime(plan, regime, ctx.config)
