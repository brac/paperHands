"""Tests for the live-cycle store + running summary — round-trip, ordering, SPY excess."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.contracts import (
    ExecutableOrder,
    ExecutablePlan,
    ProposedOrder,
    ProposedPlan,
)
from data.frame import INDEX_NAME
from record.cycle_store import CycleStore
from record.cycle_summary import summarize_cycles


def _proposed() -> ProposedPlan:
    return ProposedPlan(
        orders=(ProposedOrder("buy", "AAA", target_weight=0.1, conviction=0.8, reason="momo"),)
    )


def _gated() -> ExecutablePlan:
    return ExecutablePlan(
        orders=(ExecutableOrder("AAA", "buy", 10.0, 100.0),),
        rejected=((ProposedOrder("buy", "BBB", target_weight=0.5), "over max_position_pct"),),
    )


def _spy(dates: list[date], adj: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name=INDEX_NAME)
    return pd.DataFrame({"adj_close": adj}, index=idx)


def test_save_and_round_trip(tmp_path):
    store = CycleStore(tmp_path / "cycles.sqlite")
    cid = store.save_cycle(
        as_of=date(2024, 1, 2),
        strategy_mode="rules-only",
        snapshot_summary="3 candidates, regime ok",
        proposed=_proposed(),
        gated=_gated(),
        equity=101_000.0,
        cash=90_000.0,
        benchmark_equity=100_500.0,
    )
    loaded = store.load_cycle(cid)
    assert loaded.cycle_id == cid
    assert loaded.as_of == date(2024, 1, 2)
    assert loaded.strategy_mode == "rules-only"
    assert loaded.snapshot_summary == "3 candidates, regime ok"
    assert loaded.equity == pytest.approx(101_000.0)
    assert loaded.cash == pytest.approx(90_000.0)
    assert loaded.benchmark_equity == pytest.approx(100_500.0)
    assert loaded.proposed == _proposed()
    assert loaded.gated == _gated()


def test_latest_and_list_ordering(tmp_path):
    store = CycleStore(tmp_path / "cycles.sqlite")
    first = store.save_cycle(
        as_of=date(2024, 1, 2), strategy_mode="rules-only", snapshot_summary="day 1",
        proposed=ProposedPlan(), gated=ExecutablePlan(), equity=100_000.0, cash=100_000.0,
    )
    second = store.save_cycle(
        as_of=date(2024, 1, 3), strategy_mode="rules-only", snapshot_summary="day 2",
        proposed=ProposedPlan(), gated=ExecutablePlan(), equity=102_000.0, cash=98_000.0,
    )

    latest = store.latest_cycle()
    assert latest is not None
    assert latest.cycle_id == second

    cycles = store.list_cycles()
    assert [c.cycle_id for c in cycles] == [first, second]  # chronological (oldest first)
    assert [c.as_of for c in cycles] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_fills_written_per_order(tmp_path):
    import sqlite3

    store = CycleStore(tmp_path / "cycles.sqlite")
    store.save_cycle(
        as_of=date(2024, 1, 2), strategy_mode="rules-only", snapshot_summary="x",
        proposed=_proposed(), gated=_gated(), equity=101_000.0, cash=90_000.0,
    )
    with sqlite3.connect(str(tmp_path / "cycles.sqlite")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cycle_fills").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0] == 1


def test_empty_store_has_no_latest(tmp_path):
    store = CycleStore(tmp_path / "cycles.sqlite")
    assert store.latest_cycle() is None
    assert store.list_cycles() == []


def test_summary_excess_over_tiny_series():
    # Portfolio: 100k -> 110k (+10%); SPY: +3% over the window. Excess should be ~+7%.
    cycles = [
        (date(2024, 1, 2), 104_000.0),
        (date(2024, 1, 3), 110_000.0),
    ]
    spy = _spy([date(2024, 1, 2), date(2024, 1, 3)], [400.0, 412.0])  # +3%
    summary = summarize_cycles(cycles, starting_cash=100_000.0, spy_bars=spy)

    assert summary.n_cycles == 2
    assert summary.latest_equity == pytest.approx(110_000.0)
    assert summary.portfolio_return == pytest.approx(0.10)
    assert summary.benchmark_return == pytest.approx(412.0 / 400.0 - 1.0)
    assert summary.excess == pytest.approx(0.10 - (412.0 / 400.0 - 1.0))
    assert summary.excess > 0.0


def test_summary_empty_is_zero():
    summary = summarize_cycles([], starting_cash=100_000.0)
    assert summary.n_cycles == 0
    assert summary.portfolio_return == 0.0
    assert summary.benchmark_return == 0.0
    assert summary.excess == 0.0
    assert summary.latest_equity == pytest.approx(100_000.0)
