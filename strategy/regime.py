"""Market-regime filter — don't open new longs while the market is in a downtrend.

A pure, technicals-first risk overlay: the reference index (SPY) is ``risk_on`` when its latest
adjusted close is at or above its long moving average, ``risk_off`` below it. ``enforce_regime``
then strips *buy* orders from a proposed plan when risk-off, keeping sells/holds — so the system
stops fighting a falling market without forcing exits (the stop-loss handles those).

Computed in the composition layer (engine/cycle) from an as-of-capped SPY frame and passed into
``propose_plan``; applied as a post-filter guard so it works in both rules and llm modes. Off by
default (``StrategyConfig.regime_filter_enabled``) and fail-open when history is too short.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.config import StrategyConfig
from core.contracts import ProposedPlan
from signals.indicators import sma


@dataclass(frozen=True, slots=True)
class MarketRegime:
    """Whether the broad market is in a risk-on (uptrend) state, with the inputs for logging."""

    risk_on: bool
    reference: str = "SPY"
    price: float | None = None
    ma: float | None = None


def compute_market_regime(
    bars: pd.DataFrame, *, ma_window: int, reference: str = "SPY"
) -> MarketRegime:
    """Risk-on when the reference's latest adj close is >= its ``ma_window`` MA.

    Fails open (``risk_on=True``) when there is too little history to form the MA — a missing
    signal must never suppress trading by itself.
    """
    price = float(bars["adj_close"].iloc[-1]) if len(bars) else None
    ma = sma(bars, ma_window)
    risk_on = ma is None or price is None or price >= ma
    return MarketRegime(risk_on=risk_on, reference=reference, price=price, ma=ma)


def enforce_regime(
    plan: ProposedPlan, regime: MarketRegime | None, config: StrategyConfig
) -> ProposedPlan:
    """Drop buys when the filter is enabled and the market is risk-off; keep sells/holds."""
    if not config.regime_filter_enabled or regime is None or regime.risk_on:
        return plan
    kept = tuple(o for o in plan.orders if o.action != "buy")
    return ProposedPlan(orders=kept)
