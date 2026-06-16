"""SQLite persistence for live paper-trading cycles — one inspectable row per cycle.

Phase 2's live loop runs one cycle at a time (no upfront equity curve like the backtest),
so the store is append-only: each ``save_cycle`` writes one ``cycles`` row plus its fills.
This is a deliberately *different* table set than ``record.store.BacktestStore`` — that store
round-trips whole backtest runs; this one accumulates the daily cadence the §7 read-side
summarizes against SPY. Stdlib ``sqlite3`` only — no dependency.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from core.config import load_settings
from core.contracts import ExecutableOrder, ExecutablePlan, ProposedOrder, ProposedPlan

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY, created_at TEXT, as_of TEXT, strategy_mode TEXT,
    snapshot_summary TEXT, proposed_json TEXT, gated_json TEXT, orders_json TEXT,
    equity REAL, cash REAL, benchmark_equity REAL
);
CREATE TABLE IF NOT EXISTS cycle_fills (
    cycle_id TEXT, seq INTEGER, symbol TEXT, side TEXT, qty REAL, price REAL
);
"""


def new_cycle_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True, slots=True)
class CycleRecord:
    """One persisted cycle: its identity, the audited plans, and the marked portfolio."""

    cycle_id: str
    created_at: str  # ISO timestamp the row was written
    as_of: date
    strategy_mode: str
    snapshot_summary: str
    proposed: ProposedPlan
    gated: ExecutablePlan
    equity: float
    cash: float
    benchmark_equity: float | None = None


class CycleStore:
    """A SQLite-backed, append-only store of live paper-trading cycles."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = load_settings().record.db_path
        self._path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def save_cycle(
        self,
        *,
        as_of: date,
        strategy_mode: str,
        snapshot_summary: str,
        proposed: ProposedPlan,
        gated: ExecutablePlan,
        equity: float,
        cash: float,
        benchmark_equity: float | None = None,
        cycle_id: str | None = None,
    ) -> str:
        """Persist one cycle (and its fills, taken from ``gated.orders``); return its id."""
        cycle_id = cycle_id or new_cycle_id()
        with self._connect() as conn:
            conn.execute("DELETE FROM cycle_fills WHERE cycle_id = ?", (cycle_id,))
            conn.execute(
                "INSERT OR REPLACE INTO cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cycle_id, datetime.now().isoformat(), as_of.isoformat(), strategy_mode,
                    snapshot_summary,
                    json.dumps([asdict(o) for o in proposed.orders]),
                    json.dumps(gated.to_dict()),
                    json.dumps(gated.to_dict()["orders"]),
                    equity, cash, benchmark_equity,
                ),
            )
            conn.executemany(
                "INSERT INTO cycle_fills VALUES (?,?,?,?,?,?)",
                [
                    (cycle_id, i, o.symbol, o.side, o.qty, o.est_price)
                    for i, o in enumerate(gated.orders)
                ],
            )
        return cycle_id

    def _row_to_record(self, row: tuple) -> CycleRecord:
        (
            cycle_id, created_at, as_of, mode, snapshot_summary,
            proposed_json, gated_json, _orders_json, equity, cash, benchmark_equity,
        ) = row
        proposed = ProposedPlan(
            orders=tuple(ProposedOrder(**o) for o in json.loads(proposed_json))
        )
        gated = _plan_from_dict(json.loads(gated_json))
        return CycleRecord(
            cycle_id=cycle_id,
            created_at=created_at,
            as_of=date.fromisoformat(as_of),
            strategy_mode=mode,
            snapshot_summary=snapshot_summary,
            proposed=proposed,
            gated=gated,
            equity=equity,
            cash=cash,
            benchmark_equity=benchmark_equity,
        )

    def latest_cycle(self) -> CycleRecord | None:
        """The most recently created cycle, or ``None`` if the store is empty."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cycles ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_cycles(self) -> list[CycleRecord]:
        """All cycles in chronological order (oldest first) — the summary's input order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cycles ORDER BY as_of ASC, created_at ASC, rowid ASC"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def load_cycle(self, cycle_id: str) -> CycleRecord:
        """Reload a single cycle by id (round-trips the audited plans)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no cycle with id {cycle_id!r}")
        return self._row_to_record(row)


def _plan_from_dict(data: dict) -> ExecutablePlan:
    """Rebuild a gated plan from ``ExecutablePlan.to_dict`` (which has no inverse)."""
    orders = tuple(ExecutableOrder(**o) for o in data.get("orders", ()))
    rejected = tuple(
        (ProposedOrder(**r["order"]), r["reason"]) for r in data.get("rejected", ())
    )
    return ExecutablePlan(orders=orders, rejected=rejected)
