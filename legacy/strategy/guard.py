"""The technicals-primary guard — enforced in BOTH strategy modes.

Doctrine: technicals originate trades; news/filing flags may only adjust conviction or veto.
``has_technical_support`` is the single predicate that decides whether a *buy* has a technical
reason to exist; ``enforce_technicals_primary`` applies it to a proposed plan, dropping any
buy that lacks support (or lacks a SignalSet entirely). Sells and holds always pass — exits
reduce risk and never need a technical thesis.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.config import StrategyConfig
from core.contracts import ProposedOrder, ProposedPlan
from signals.signalset import SignalSet


def has_technical_support(signal: SignalSet, config: StrategyConfig) -> bool:
    """True if a buy in ``signal`` is justified by technicals (momentum OR mean-reversion).

    - Momentum: ``roc > momentum_buy_threshold`` AND ``trend_strength > 0``.
    - Mean-reversion: ``zscore < zscore_oversold`` (oversold).
    None-valued indicators (insufficient history) do not count as support.
    """
    momentum = (
        signal.roc is not None
        and signal.trend_strength is not None
        and signal.roc > config.momentum_buy_threshold
        and signal.trend_strength > 0.0
    )
    mean_reversion = signal.zscore is not None and signal.zscore < config.zscore_oversold
    return bool(momentum or mean_reversion)


def enforce_technicals_primary(
    plan: ProposedPlan,
    signals: Mapping[str, SignalSet],
    config: StrategyConfig,
) -> ProposedPlan:
    """Drop any buy without a SignalSet or without technical support; keep sells/holds.

    This is the deterministic backstop that makes "news may not originate a trade" true
    regardless of mode — it catches a news-only rule slip or an LLM hallucination alike.
    """
    kept: list[ProposedOrder] = []
    for order in plan.orders:
        if order.action != "buy":
            kept.append(order)
            continue
        signal = signals.get(order.symbol)
        if signal is not None and has_technical_support(signal, config):
            kept.append(order)
        # else: news-only / unsupported buy -> dropped
    return ProposedPlan(orders=tuple(kept))
