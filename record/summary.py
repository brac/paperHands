"""The run summary — the headline object the report renders and the store round-trips."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from record.stats import PerformanceStats


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Portfolio vs benchmark headline for one backtest run."""

    run_id: str
    start: date
    end: date
    starting_cash: float
    strategy_mode: str
    n_steps: int
    n_fills: int
    portfolio_final: float
    benchmark_final: float
    portfolio_stats: PerformanceStats
    benchmark_stats: PerformanceStats

    @property
    def excess_return(self) -> float:
        """Portfolio total return minus SPY total return over the identical window."""
        return self.portfolio_stats.total_return - self.benchmark_stats.total_return
