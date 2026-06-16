"""Tests for the walk-forward harness — rolling-window generation + OOS distribution report.

Pure/offline: the rolling-window math and the formatter are exercised over hand-built
EvaluationResults; the per-window backtest itself is already covered by test_runner.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from record.stats import PerformanceStats
from record.summary import RunSummary
from runner.evaluate import EvaluationResult, WindowOutcome
from runner.walkforward import format_walkforward, rolling_windows
from runner.windows import Window


# -- rolling_windows ---------------------------------------------------------------------
def test_rolling_windows_non_overlapping_cover_range():
    wins = rolling_windows(date(2020, 1, 1), date(2020, 12, 31), test_days=90, step_days=90)
    assert [w.label for w in wins] == ["wf-001", "wf-002", "wf-003", "wf-004"]
    assert all(w.regime == "oos" for w in wins)
    # Each window spans exactly test_days and never runs past end.
    for w in wins:
        assert (w.end - w.start).days == 90
        assert w.end <= date(2020, 12, 31)
    # Stepped by step_days.
    assert wins[1].start == date(2020, 1, 1) + timedelta(days=90)


def test_rolling_windows_overlap_when_step_below_test():
    wins = rolling_windows(date(2020, 1, 1), date(2020, 7, 1), test_days=90, step_days=30)
    starts = [w.start for w in wins]
    assert starts == sorted(starts) and len(wins) >= 3
    assert wins[1].start == date(2020, 1, 31)  # stepped 30 days, windows overlap


def test_rolling_windows_empty_when_range_too_short():
    assert rolling_windows(date(2020, 1, 1), date(2020, 2, 1), test_days=90, step_days=90) == ()


def test_rolling_windows_rejects_nonpositive():
    with pytest.raises(ValueError, match="positive"):
        rolling_windows(date(2020, 1, 1), date(2020, 12, 31), test_days=0, step_days=90)


# -- format_walkforward ------------------------------------------------------------------
def _summary(label: str, *, excess: float, sharpe: float, dd: float) -> RunSummary:
    # portfolio total_return = excess, benchmark total_return = 0 -> excess_return = excess.
    pstats = PerformanceStats(excess, 0.0, 0.0, dd, sharpe, 0.0, 0.0)
    bstats = PerformanceStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return RunSummary(
        label, date(2021, 1, 1), date(2021, 6, 30), 100_000.0, "rules-only",
        1, 1, 110_000.0, 100_000.0, pstats, bstats,
    )


def _result(*outcomes: WindowOutcome) -> EvaluationResult:
    return EvaluationResult(tuple(outcomes), "rules-only", 41)


def _win(label: str) -> Window:
    return Window(label, date(2021, 1, 1), date(2021, 6, 30), "oos")


def _outcome(label: str, *, excess: float, sharpe: float, dd: float) -> WindowOutcome:
    return WindowOutcome(_win(label), summary=_summary(label, excess=excess, sharpe=sharpe, dd=dd))


def test_format_reports_distribution_and_positive_verdict():
    result = _result(
        _outcome("wf-001", excess=0.10, sharpe=1.0, dd=-0.05),
        _outcome("wf-002", excess=-0.04, sharpe=-0.2, dd=-0.10),
        _outcome("wf-003", excess=0.02, sharpe=0.5, dd=-0.03),
    )
    text = format_walkforward(result)
    assert "Beat SPY:      2/3 (67%)" in text
    assert "median +2.00%" in text
    assert "Worst wf-002 -4.00%" in text and "Best wf-001 +10.00%" in text
    assert "Verdict: edge holds out-of-sample" in text


def test_format_negative_verdict_when_inconsistent():
    result = _result(
        _outcome("wf-001", excess=-0.05, sharpe=-0.1, dd=-0.2),
        _outcome("wf-002", excess=0.01, sharpe=0.1, dd=-0.05),
    )
    text = format_walkforward(result)
    assert "Verdict: no consistent out-of-sample edge" in text


def test_format_handles_failed_and_empty():
    failed = _result(WindowOutcome(_win("wf-001"), error="RuntimeError: boom"))
    text = format_walkforward(failed)
    assert "FAILED" in text
    assert "no successful windows" in text
