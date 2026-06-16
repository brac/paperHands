"""Tests for runner.schedule — one cycle per open session, driven by a scripted clock.

No real sleeping and no network: the clock, sleep, now, and cycle calls are all injected. Each
scripted clock list is sized to the exact number of loop iterations the bounded run performs.
"""

from __future__ import annotations

from datetime import datetime

from broker import MarketClock
from core.config import Settings
from runner.schedule import run_scheduler


def _clk(is_open: bool, session: int) -> MarketClock:
    """A clock for trading session ``session`` (distinct ``next_close.date()`` per session)."""
    day = 10 + session
    return MarketClock(
        is_open=is_open,
        next_open=datetime(2026, 6, day, 9, 30),
        next_close=datetime(2026, 6, day, 16, 0),
    )


class _ScriptedClock:
    """Returns successive clocks from a list; over-reading signals a loop-logic bug."""

    def __init__(self, clocks: list[MarketClock]) -> None:
        self._clocks = clocks
        self.calls = 0

    def __call__(self) -> MarketClock:
        clock = self._clocks[self.calls]
        self.calls += 1
        return clock


class _Sleeps:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, seconds: float) -> None:
        self.count += 1


class _Cycles:
    """Records cycle invocations; optionally raises on the first call."""

    def __init__(self, raise_first: bool = False) -> None:
        self.count = 0
        self._raise_first = raise_first

    def __call__(self) -> object:
        self.count += 1
        if self._raise_first and self.count == 1:
            raise RuntimeError("transient cycle failure")
        return f"cycle-{self.count}"


def _now() -> datetime:
    return datetime(2026, 6, 17, 9, 0)


def _run(clocks, cycles, sleeps, *, max_cycles):  # noqa: ANN001, ANN003
    return run_scheduler(
        Settings(), broker=object(), max_cycles=max_cycles,
        clock_fn=_ScriptedClock(clocks), sleep_fn=sleeps, now_fn=_now, cycle_fn=cycles,
    )


def test_runs_once_per_open_session_and_skips_closed():
    cycles, sleeps = _Cycles(), _Sleeps()
    # closed (wait), then open session 1 (run -> hits max_cycles).
    n = _run([_clk(False, 1), _clk(True, 1)], cycles, sleeps, max_cycles=1)
    assert n == 1
    assert cycles.count == 1  # the closed iteration did not run a cycle
    assert sleeps.count == 1  # it slept while the market was closed


def test_same_session_does_not_double_run():
    cycles, sleeps = _Cycles(), _Sleeps()
    # open S1 (run), open S1 again (idle/sleep), open S2 (run -> hits max_cycles).
    n = _run([_clk(True, 1), _clk(True, 1), _clk(True, 2)], cycles, sleeps, max_cycles=2)
    assert n == 2
    assert cycles.count == 2  # ran once per distinct session, not on the repeat
    assert sleeps.count == 1  # the repeat-of-session iteration idled


def test_cycle_exception_does_not_kill_the_loop():
    cycles, sleeps = _Cycles(raise_first=True), _Sleeps()
    # S1 raises (caught), S2 succeeds; the loop survives and still counts both sessions.
    n = _run([_clk(True, 1), _clk(True, 2)], cycles, sleeps, max_cycles=2)
    assert n == 2
    assert cycles.count == 2
