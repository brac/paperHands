"""Tests for the read-only dashboard export and the realized-P&L helper.

Seeds a BacktestStore with a known run, then asserts ``build_export`` produces the documented
JSON shape: equity curve, stats pass-through, positions reconstructed from fills with drift,
and trades with average-cost realized P&L.
"""

from __future__ import annotations

from datetime import date

from broker.simulated import EquityPoint, Fill
from core.config import RebalanceConfig, Settings
from dashboard.export import build_export
from engine.result import BacktestResult
from record import BacktestStore
from record.stats import PerformanceStats, realized_pnl_by_fill
from record.summary import RunSummary


def _stats(total_return: float, max_dd: float) -> PerformanceStats:
    return PerformanceStats(total_return, 0.0, 0.1, max_dd, 1.0, 0.5, 0.2)


def _seed_store(path: str) -> str:
    store = BacktestStore(path)
    run_id = "testrun01"
    summary = RunSummary(
        run_id=run_id, start=date(2020, 1, 1), end=date(2020, 1, 3),
        starting_cash=100_000.0, strategy_mode="rebalance", n_steps=2, n_fills=3,
        portfolio_final=110_000.0, benchmark_final=108_000.0,
        portfolio_stats=_stats(0.10, -0.08), benchmark_stats=_stats(0.08, -0.15),
    )
    result = BacktestResult(
        equity_curve=(
            EquityPoint(date(2020, 1, 1), 100_000.0, 100_000.0),
            EquityPoint(date(2020, 1, 2), 105_000.0, 50_000.0),
            EquityPoint(date(2020, 1, 3), 110_000.0, 4_000.0),
        ),
        steps=(),
        fills=(
            Fill("SPY", "buy", 600.0, 100.0, 0.0),   # 60k
            Fill("BND", "buy", 360.0, 100.0, 0.0),   # 36k
            Fill("SPY", "sell", 60.0, 110.0, 0.0),   # sell 60 @ 110, cost 100 -> +600 realized
        ),
        start=date(2020, 1, 1), end=date(2020, 1, 3),
    )
    benchmark_curve = [
        (date(2020, 1, 1), 100_000.0),
        (date(2020, 1, 2), 104_000.0),
        (date(2020, 1, 3), 108_000.0),
    ]
    store.save_run(summary, result, benchmark_curve)
    return run_id


def _settings() -> Settings:
    return Settings(rebalance=RebalanceConfig(target_weights={"SPY": 0.6, "BND": 0.4}))


def test_export_shape_and_passthrough(tmp_path):
    db = str(tmp_path / "results.sqlite")
    run_id = _seed_store(db)
    doc = build_export(db, run_id, _settings())

    assert doc["run"]["run_id"] == run_id
    assert doc["run"]["strategy_mode"] == "rebalance"
    assert "SPY" in doc["benchmark_label"]
    # Equity curve carries both portfolio and benchmark series.
    assert len(doc["equity_curve"]) == 3
    assert doc["equity_curve"][-1]["equity"] == 110_000.0
    assert doc["equity_curve"][-1]["benchmark_equity"] == 108_000.0
    # Stats are passed through from the stored run (no recomputation).
    assert doc["stats"]["portfolio"]["max_drawdown"] == -0.08
    assert doc["stats"]["benchmark"]["max_drawdown"] == -0.15
    assert doc["target_weights"] == {"SPY": 0.6, "BND": 0.4}


def test_export_positions_reconstructed_with_drift(tmp_path):
    db = str(tmp_path / "results.sqlite")
    run_id = _seed_store(db)
    doc = build_export(db, run_id, _settings())
    pos = {p["symbol"]: p for p in doc["positions"]}

    # SPY: bought 600, sold 60 -> 540 held @ last price 110 = 59_400; final equity 110_000.
    assert pos["SPY"]["qty"] == 540.0
    assert pos["SPY"]["current_value"] == 540.0 * 110.0
    assert pos["SPY"]["current_weight"] == (540.0 * 110.0) / 110_000.0
    assert pos["SPY"]["target_weight"] == 0.6
    assert pos["SPY"]["drift"] == pos["SPY"]["current_weight"] - 0.6
    # BND: 360 held @ 100 = 36_000.
    assert pos["BND"]["qty"] == 360.0


def test_export_trades_have_realized_pnl(tmp_path):
    db = str(tmp_path / "results.sqlite")
    run_id = _seed_store(db)
    doc = build_export(db, run_id, _settings())
    trades = doc["trades"]
    assert len(trades) == 3
    sell = next(t for t in trades if t["side"] == "sell")
    assert sell["realized_pnl"] == 600.0  # 60 * (110 - 100)
    assert all(t["realized_pnl"] == 0.0 for t in trades if t["side"] == "buy")


def test_export_defaults_to_latest_run(tmp_path):
    db = str(tmp_path / "results.sqlite")
    run_id = _seed_store(db)
    doc = build_export(db, None, _settings())  # None -> latest
    assert doc["run"]["run_id"] == run_id


# -- realized_pnl_by_fill (average-cost lot matching) --------------------------------
def test_realized_pnl_average_cost():
    fills = (
        Fill("X", "buy", 10.0, 100.0, 0.0),
        Fill("X", "buy", 10.0, 120.0, 0.0),   # avg cost now 110
        Fill("X", "sell", 15.0, 130.0, 0.0),  # 15 * (130 - 110) = 300
    )
    pnl = realized_pnl_by_fill(fills)
    assert pnl == [0.0, 0.0, 300.0]


def test_realized_pnl_folds_commission_into_basis():
    fills = (
        Fill("X", "buy", 10.0, 100.0, 50.0),   # basis 1050 over 10 sh -> avg 105
        Fill("X", "sell", 10.0, 110.0, 10.0),  # 10*(110-105) - 10 commission = 40
    )
    assert realized_pnl_by_fill(fills) == [0.0, 40.0]
