"""Tests for the live-cycle read-side — SPY benchmark wiring + offline fallback.

All offline: the data provider is injected (a synthetic SPY frame or a raising stub), so the
report's benchmark path is exercised without network.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from core.config import Settings
from core.contracts import ExecutablePlan, ProposedPlan
from data.frame import COLUMNS, INDEX_NAME
from record.cycle_store import CycleStore
from runner.report import build_cycle_report

_D1 = date(2024, 5, 20)
_D2 = date(2024, 5, 21)


def _spy_frame() -> pd.DataFrame:
    """A two-bar SPY frame whose adj_close rises 10% across the cycle dates."""
    idx = pd.DatetimeIndex([pd.Timestamp(_D1), pd.Timestamp(_D2)], name=INDEX_NAME)
    data = {c: [400.0, 440.0] for c in COLUMNS}
    return pd.DataFrame(data, index=idx)


class _StubProvider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def get_daily_bars(self, symbol, start, end, *, as_of=None):  # noqa: ANN001, ANN204
        return self._frame


class _RaisingProvider:
    def get_daily_bars(self, symbol, start, end, *, as_of=None):  # noqa: ANN001, ANN204
        raise RuntimeError("no TIINGO_API_KEY")


def _store_two_cycles(tmp_path) -> CycleStore:  # noqa: ANN001
    store = CycleStore(tmp_path / "cycles.sqlite")
    for d, equity in ((_D1, 100_000.0), (_D2, 110_000.0)):
        store.save_cycle(
            as_of=d, strategy_mode="rules-only", snapshot_summary=f"snap {d}",
            proposed=ProposedPlan(), gated=ExecutablePlan(), equity=equity, cash=equity,
        )
    return store


def test_report_includes_real_spy_benchmark(tmp_path):
    store = _store_two_cycles(tmp_path)
    report = build_cycle_report(store, Settings(), provider=_StubProvider(_spy_frame()))
    assert "SPY return:" in report
    assert "SPY return:       +0.00%" not in report  # a real, non-zero SPY return is shown


def test_report_degrades_gracefully_when_spy_unavailable(tmp_path):
    store = _store_two_cycles(tmp_path)
    report = build_cycle_report(store, Settings(), provider=_RaisingProvider())
    # No crash; SPY column falls back to 0.00% rather than failing the report.
    assert "SPY return:       +0.00%" in report


def test_report_handles_no_cycles(tmp_path):
    store = CycleStore(tmp_path / "empty.sqlite")
    assert build_cycle_report(store, Settings()) == "no cycles recorded yet."
