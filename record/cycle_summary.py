"""Pure running portfolio-vs-SPY summary over a sequence of stored cycles.

The live loop persists one (as_of, equity) point per cycle; this folds that ordered series
into a headline the §7 read-side prints. It reuses ``compute_stats`` for the portfolio's
total return and ``compute_benchmark`` for SPY (when bars are supplied), so the live summary
and the backtest report agree by construction. Pure and total — an empty/short series yields
zeros rather than raising.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from record.benchmark import compute_benchmark
from record.stats import compute_stats


@dataclass(frozen=True, slots=True)
class CycleSummary:
    """Running portfolio-vs-SPY headline across all recorded cycles."""

    portfolio_return: float
    benchmark_return: float
    excess: float
    n_cycles: int
    latest_equity: float


def summarize_cycles(
    cycles: Sequence[tuple[date, float]],
    starting_cash: float,
    spy_bars: pd.DataFrame | None = None,
) -> CycleSummary:
    """Fold ordered ``(as_of, equity)`` points into a portfolio-vs-SPY summary.

    ``cycles`` must be in chronological order. The portfolio return is anchored to
    ``starting_cash`` (cycle equities are absolute dollar marks). The benchmark return is
    computed from ``spy_bars`` over the same dates; with no bars it stays ``0.0``.
    """
    if not cycles:
        return CycleSummary(0.0, 0.0, 0.0, 0, starting_cash)

    dates = [d for d, _ in cycles]
    equities = [starting_cash, *(e for _, e in cycles)]
    latest_equity = equities[-1]

    portfolio_return = compute_stats(equities).total_return

    benchmark_return = 0.0
    if spy_bars is not None and not spy_bars.empty:
        bench_curve = compute_benchmark(spy_bars, dates, starting_cash)
        bench_equities = [starting_cash, *(v for _, v in bench_curve)]
        benchmark_return = compute_stats(bench_equities).total_return

    return CycleSummary(
        portfolio_return=portfolio_return,
        benchmark_return=benchmark_return,
        excess=portfolio_return - benchmark_return,
        n_cycles=len(cycles),
        latest_equity=latest_equity,
    )
