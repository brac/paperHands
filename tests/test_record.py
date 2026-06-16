"""Tests for record & benchmark — stats, SPY benchmark, SQLite store, recorder, report."""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest

from broker.simulated import EquityPoint, Fill
from core.config import RecordConfig, Settings
from core.contracts import ExecutablePlan, ProposedPlan
from data.frame import INDEX_NAME
from engine.result import BacktestResult, StepRecord
from record import BacktestStore, compute_stats, format_report, record_run
from record.benchmark import compute_benchmark
from record.stats import PerformanceStats
from record.summary import RunSummary


# --------------------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------------------
def test_stats_hand_computed():
    stats = compute_stats([100.0, 110.0, 99.0, 108.0], [Fill("X", "buy", 10.0, 100.0, 0.0)])
    assert stats.total_return == pytest.approx(0.08)
    assert stats.max_drawdown == pytest.approx(99.0 / 110.0 - 1.0)  # -0.0909...
    assert stats.hit_rate == pytest.approx(2 / 3)  # 2 of 3 up days
    assert stats.turnover == pytest.approx(1000.0 / ((100 + 110 + 99 + 108) / 4))
    assert stats.volatility > 0.0
    assert stats.cagr > 0.0


def test_stats_degenerate_inputs_are_zero():
    for series in ([], [100.0], [100.0, 100.0, 100.0]):
        stats = compute_stats(series)
        assert stats.total_return == 0.0
        assert stats.volatility == 0.0
        assert stats.sharpe == 0.0
        assert stats.max_drawdown == 0.0


# --------------------------------------------------------------------------------------
# Benchmark
# --------------------------------------------------------------------------------------
def _spy(dates: list[date], adj: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name=INDEX_NAME)
    return pd.DataFrame({"adj_close": adj}, index=idx)


def test_benchmark_scales_by_total_return():
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    curve = compute_benchmark(_spy(dates, [100.0, 110.0, 120.0]), dates, 1_000.0)
    assert curve[0] == (dates[0], pytest.approx(1_000.0))   # anchored
    assert curve[1][1] == pytest.approx(1_100.0)
    assert curve[2][1] == pytest.approx(1_200.0)


# --------------------------------------------------------------------------------------
# Store + recorder + report
# --------------------------------------------------------------------------------------
def _result(dates: list[date], equities: list[float]) -> BacktestResult:
    curve = tuple(EquityPoint(d, e, e) for d, e in zip(dates, equities, strict=True))
    steps = (StepRecord(dates[0], ("AAA",), ProposedPlan(), ExecutablePlan(), equities[0],
                        equities[0]),)
    fills = (Fill("AAA", "buy", 1.0, 100.0, 0.0),)
    return BacktestResult(curve, steps, fills, dates[0], dates[-1])


def _settings(tmp_path) -> Settings:
    return Settings(record=RecordConfig(db_path=str(tmp_path / "r.sqlite")))


class _FakeProvider:
    def __init__(self, spy: pd.DataFrame) -> None:
        self._spy = spy

    def get_daily_bars(self, symbol, start, end, *, as_of=None):
        return self._spy


_DATES = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def test_store_round_trip(tmp_path):
    store = BacktestStore(tmp_path / "r.sqlite")
    stats = compute_stats([100.0, 110.0])
    summary = RunSummary(
        run_id="abc123", start=_DATES[0], end=_DATES[-1], starting_cash=100_000.0,
        strategy_mode="rules-only", n_steps=1, n_fills=1,
        portfolio_final=108_000.0, benchmark_final=105_000.0,
        portfolio_stats=stats, benchmark_stats=stats,
    )
    result = _result(_DATES, [100_000.0, 104_000.0, 108_000.0])
    bench = [(d, v) for d, v in zip(_DATES, [100_000.0, 102_500.0, 105_000.0], strict=True)]
    store.save_run(summary, result, bench)

    assert store.latest_run_id() == "abc123"
    assert store.load_summary("abc123") == summary

    with sqlite3.connect(str(tmp_path / "r.sqlite")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM equity_points").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_record_run_end_to_end(tmp_path):
    settings = _settings(tmp_path)
    provider = _FakeProvider(_spy(_DATES, [400.0, 408.0, 412.0]))  # SPY +3%
    result = _result(_DATES, [100_000.0, 109_000.0, 110_000.0])    # portfolio +10%
    summary = record_run(result, provider, settings, store=BacktestStore(settings.record.db_path))

    assert summary.portfolio_final == pytest.approx(110_000.0)
    assert summary.benchmark_final == pytest.approx(100_000.0 * 412.0 / 400.0)
    assert summary.excess_return > 0.0  # portfolio beat SPY over the window
    # Persisted + reloadable.
    assert BacktestStore(settings.record.db_path).load_summary(summary.run_id) == summary


def test_format_report_contains_key_fields():
    stats = PerformanceStats(0.10, 0.5, 0.2, -0.05, 1.2, 0.6, 3.0)
    summary = RunSummary(
        run_id="r1", start=_DATES[0], end=_DATES[-1], starting_cash=100_000.0,
        strategy_mode="rules-only", n_steps=3, n_fills=2,
        portfolio_final=110_000.0, benchmark_final=103_000.0,
        portfolio_stats=stats, benchmark_stats=stats,
    )
    text = format_report(summary)
    assert "Portfolio" in text and "SPY" in text
    assert "Total return" in text and "Max drawdown" in text
    assert "Excess vs SPY" in text
    assert "2024-01-02 .. 2024-01-04" in text
