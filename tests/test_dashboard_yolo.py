"""Test the combined dashboard export — the YOLO overlay (third line) on the baseline doc.

Seeds the store with a baseline run and a separate YOLO run sharing the same dates, then asserts
``build_export(..., yolo_run_id=...)`` overlays the YOLO equity onto each point as ``yolo_equity``,
aligns by ts (missing dates -> None), and adds the YOLO stats column + honest "proxy hype" label.
"""

from __future__ import annotations

from datetime import date

from broker.simulated import EquityPoint, Fill
from core.config import RebalanceConfig, Settings
from dashboard.export import build_export
from engine.result import BacktestResult
from record import BacktestStore
from record.stats import PerformanceStats
from record.summary import RunSummary


def _stats(total_return: float, max_dd: float) -> PerformanceStats:
    return PerformanceStats(total_return, 0.0, 0.1, max_dd, 1.0, 0.5, 0.2)


def _seed_run(store, run_id, mode, equities, fills=()):
    dates = [date(2020, 1, d) for d in range(1, len(equities) + 1)]
    summary = RunSummary(
        run_id=run_id, start=dates[0], end=dates[-1],
        starting_cash=100_000.0, strategy_mode=mode, n_steps=0, n_fills=len(fills),
        portfolio_final=equities[-1], benchmark_final=108_000.0,
        portfolio_stats=_stats(equities[-1] / 100_000.0 - 1.0, -0.30 if mode == "yolo" else -0.08),
        benchmark_stats=_stats(0.08, -0.15),
    )
    result = BacktestResult(
        equity_curve=tuple(
            EquityPoint(d, e, e * 0.1) for d, e in zip(dates, equities, strict=True)),
        steps=(), fills=tuple(fills), start=dates[0], end=dates[-1],
    )
    bench = [(d, 100_000.0 + 4_000.0 * i) for i, d in enumerate(dates)]
    store.save_run(summary, result, bench)


def _settings() -> Settings:
    return Settings(rebalance=RebalanceConfig(target_weights={"SPY": 0.6, "BND": 0.4}))


def test_combined_export_overlays_yolo_series_and_stats(tmp_path):
    db = str(tmp_path / "results.sqlite")
    store = BacktestStore(db)
    _seed_run(store, "base1", "rebalance", [100_000.0, 105_000.0, 110_000.0],
              fills=(Fill("SPY", "buy", 600.0, 100.0, 0.0),))
    _seed_run(store, "yolo1", "yolo", [100_000.0, 130_000.0, 70_000.0])  # wild ride

    doc = build_export(db, "base1", _settings(), yolo_run_id="yolo1")

    # The baseline + benchmark series are unchanged; the YOLO series is overlaid by ts.
    assert [p["equity"] for p in doc["equity_curve"]] == [100_000.0, 105_000.0, 110_000.0]
    assert [p["yolo_equity"] for p in doc["equity_curve"]] == [100_000.0, 130_000.0, 70_000.0]
    # Stats gain a third (YOLO) column and the run is tagged honestly.
    assert doc["stats"]["yolo"]["max_drawdown"] == -0.30
    assert doc["yolo_label"] == "YOLO (proxy hype)"
    assert doc["yolo_run"]["run_id"] == "yolo1"


def test_combined_export_aligns_by_ts_with_none_for_missing_dates(tmp_path):
    db = str(tmp_path / "results.sqlite")
    store = BacktestStore(db)
    _seed_run(store, "base1", "rebalance", [100_000.0, 105_000.0, 110_000.0])
    _seed_run(store, "yolo1", "yolo", [100_000.0, 130_000.0])  # one date shorter

    doc = build_export(db, "base1", _settings(), yolo_run_id="yolo1")
    # Baseline's third date has no YOLO point -> None (the chart breaks the line there).
    assert doc["equity_curve"][-1]["yolo_equity"] is None


def test_export_without_yolo_run_has_no_yolo_keys(tmp_path):
    db = str(tmp_path / "results.sqlite")
    store = BacktestStore(db)
    _seed_run(store, "base1", "rebalance", [100_000.0, 110_000.0])
    doc = build_export(db, "base1", _settings())
    assert "yolo_label" not in doc
    assert "yolo" not in doc["stats"]
    assert "yolo_equity" not in doc["equity_curve"][0]
