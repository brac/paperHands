"""Pure performance statistics over an equity series.

All functions are pure and total — degenerate inputs (fewer than two points, a flat series,
zero variance, non-positive equity) yield ``0.0`` rather than NaN/inf, so a report never shows
garbage. Annualization uses 252 trading days; the risk-free rate is 0.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from broker.simulated import Fill

_TRADING_DAYS = 252


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    """Headline stats for one equity curve."""

    total_return: float
    cagr: float
    volatility: float
    max_drawdown: float  # negative fraction (e.g. -0.12 = -12%)
    sharpe: float
    hit_rate: float
    turnover: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _daily_returns(equities: Sequence[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(equities, equities[1:], strict=False):
        out.append(cur / prev - 1.0 if prev > 0 else 0.0)
    return out


def _max_drawdown(equities: Sequence[float]) -> float:
    peak = equities[0]
    worst = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def compute_stats(
    equities: Sequence[float], fills: Sequence[Fill] = ()
) -> PerformanceStats:
    """Compute headline stats for an equity series (+ fills, for turnover)."""
    if len(equities) < 2 or equities[0] <= 0:
        return PerformanceStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    initial, final = equities[0], equities[-1]
    returns = _daily_returns(equities)
    periods = len(returns)

    total_return = final / initial - 1.0
    cagr = (final / initial) ** (_TRADING_DAYS / periods) - 1.0 if final > 0 else -1.0

    std = statistics.stdev(returns) if periods >= 2 else 0.0
    mean = statistics.fmean(returns)
    volatility = std * math.sqrt(_TRADING_DAYS)
    sharpe = (mean / std) * math.sqrt(_TRADING_DAYS) if std > 0 else 0.0

    hit_rate = sum(1 for r in returns if r > 0) / periods
    max_drawdown = _max_drawdown(equities)

    traded_notional = sum(abs(f.qty * f.price) for f in fills)
    mean_equity = statistics.fmean(equities)
    turnover = traded_notional / mean_equity if mean_equity > 0 else 0.0

    return PerformanceStats(
        total_return=total_return,
        cagr=cagr,
        volatility=volatility,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        hit_rate=hit_rate,
        turnover=turnover,
    )
