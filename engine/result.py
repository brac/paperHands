"""Backtest result contracts — what the engine produces and §9 will persist/benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from broker.simulated import EquityPoint, Fill
from core.contracts import ExecutablePlan, ProposedPlan


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One decision bar: the inputs the strategy saw and the plan it produced (pre/post gate)."""

    as_of: date
    candidates: tuple[str, ...]
    proposed: ProposedPlan
    gated: ExecutablePlan
    equity: float
    cash: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The full run: equity curve (every bar), decision records, and all fills."""

    equity_curve: tuple[EquityPoint, ...]
    steps: tuple[StepRecord, ...]
    fills: tuple[Fill, ...]
    start: date
    end: date

    def final_equity(self) -> float:
        return self.equity_curve[-1].equity if self.equity_curve else 0.0
