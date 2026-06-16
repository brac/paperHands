"""SQLite persistence for backtest runs — the inspectable store §10/reporting reads.

One file (`results.sqlite` by default) with four tables: ``runs`` (summary + stats JSON),
``equity_points`` (portfolio + benchmark curve), ``steps`` (per-decision records), and
``fills``. Stdlib ``sqlite3`` only — no dependency.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from engine.result import BacktestResult
from record.stats import PerformanceStats
from record.summary import RunSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, created_at TEXT, start TEXT, end TEXT, starting_cash REAL,
    strategy_mode TEXT, n_steps INTEGER, n_fills INTEGER,
    portfolio_final REAL, benchmark_final REAL,
    portfolio_stats_json TEXT, benchmark_stats_json TEXT
);
CREATE TABLE IF NOT EXISTS equity_points (
    run_id TEXT, ts TEXT, equity REAL, cash REAL, benchmark_equity REAL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT, as_of TEXT, candidates_json TEXT, proposed_json TEXT, gated_json TEXT,
    equity REAL, cash REAL
);
CREATE TABLE IF NOT EXISTS fills (
    run_id TEXT, seq INTEGER, symbol TEXT, side TEXT, qty REAL, price REAL, commission REAL
);
"""


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class BacktestStore:
    """A SQLite-backed store of backtest runs."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def save_run(
        self,
        summary: RunSummary,
        result: BacktestResult,
        benchmark_curve: Sequence[tuple[date, float]],
    ) -> None:
        bench_by_date = {d: e for d, e in benchmark_curve}
        with self._connect() as conn:
            for table in ("equity_points", "steps", "fills"):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (summary.run_id,))
            conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    summary.run_id, datetime.now().isoformat(),
                    summary.start.isoformat(), summary.end.isoformat(),
                    summary.starting_cash, summary.strategy_mode,
                    summary.n_steps, summary.n_fills,
                    summary.portfolio_final, summary.benchmark_final,
                    json.dumps(summary.portfolio_stats.to_dict()),
                    json.dumps(summary.benchmark_stats.to_dict()),
                ),
            )
            conn.executemany(
                "INSERT INTO equity_points VALUES (?,?,?,?,?)",
                [
                    (summary.run_id, p.timestamp.isoformat(), p.equity, p.cash,
                     bench_by_date.get(p.timestamp))
                    for p in result.equity_curve
                ],
            )
            conn.executemany(
                "INSERT INTO steps VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        summary.run_id, s.as_of.isoformat(),
                        json.dumps(list(s.candidates)),
                        json.dumps([asdict(o) for o in s.proposed.orders]),
                        json.dumps(s.gated.to_dict()),
                        s.equity, s.cash,
                    )
                    for s in result.steps
                ],
            )
            conn.executemany(
                "INSERT INTO fills VALUES (?,?,?,?,?,?,?)",
                [
                    (summary.run_id, i, f.symbol, f.side, f.qty, f.price, f.commission)
                    for i, f in enumerate(result.fills)
                ],
            )

    def latest_run_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def list_runs(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC").fetchall()
        return [r[0] for r in rows]

    def load_summary(self, run_id: str) -> RunSummary:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no run with id {run_id!r}")
        (
            _id, _created, start, end, starting_cash, mode, n_steps, n_fills,
            pf, bf, pstats_json, bstats_json,
        ) = row
        return RunSummary(
            run_id=_id,
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            starting_cash=starting_cash,
            strategy_mode=mode,
            n_steps=n_steps,
            n_fills=n_fills,
            portfolio_final=pf,
            benchmark_final=bf,
            portfolio_stats=PerformanceStats(**json.loads(pstats_json)),
            benchmark_stats=PerformanceStats(**json.loads(bstats_json)),
        )
