"""propose_plan — the swappable, dual-mode strategy entry point.

The same pure function runs in backtest, paper, and live. It dispatches on the context's
mode, then applies the sovereign-doctrine guard (``enforce_technicals_primary``) so a
news-only or hallucinated buy can never survive, regardless of mode. The risk gate is still
the final authority downstream; this only produces the *proposal*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.contracts import Position, ProposedPlan
from signals.signalset import SignalSet
from strategy.context import StrategyContext
from strategy.guard import enforce_technicals_primary
from strategy.llm import llm_propose
from strategy.regime import MarketRegime, enforce_regime
from strategy.rules import rules_propose


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
    if ctx.mode == "llm":
        if ctx.llm_client is None:
            plan = ProposedPlan()  # misconfigured llm mode -> safe empty plan
        else:
            plan = llm_propose(signals, positions, cash, ctx.config, ctx.llm_client)
    else:  # rules-only
        plan = rules_propose(signals, positions, cash, ctx.config)

    plan = enforce_technicals_primary(plan, signals, ctx.config)
    return enforce_regime(plan, regime, ctx.config)
