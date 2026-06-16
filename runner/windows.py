"""Predefined regime windows for multi-window evaluation.

Edge can't be judged on one window — a momentum strategy that lags a melt-up might still earn
its keep by cutting risk in a drawdown. These default windows span distinct US-equity regimes
so the evaluation sees the strategy across conditions. They are the user's to tune.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Window:
    """A named backtest window with a regime label (for the evaluation table)."""

    label: str
    start: date
    end: date
    regime: str


DEFAULT_WINDOWS: tuple[Window, ...] = (
    Window("2020-covid-crash", date(2020, 2, 14), date(2020, 4, 30), "drawdown"),
    Window("2021-bull", date(2021, 1, 1), date(2021, 12, 31), "bull"),
    Window("2022-bear", date(2022, 1, 1), date(2022, 12, 31), "bear"),
    Window("2023-recovery", date(2023, 1, 1), date(2023, 12, 31), "recovery/chop"),
)
