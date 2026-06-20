"""Single-run orchestration: build engine -> run -> record. Reused by both CLIs.

Pure composition over existing pieces — no new business logic. Sharing a ``provider`` keeps
the data cache warm across runs (e.g. a multi-window evaluation).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from core.config import Settings
from data import build_data_provider
from data.base import DataProvider
from engine import build_engine
from record import BacktestStore, record_run
from record.summary import RunSummary
from strategy import LLMClient


def run_backtest(
    settings: Settings,
    start: date,
    end: date,
    universe: Sequence[str] | None = None,
    *,
    provider: DataProvider | None = None,
    store: BacktestStore | None = None,
    run_id: str | None = None,
    llm_client: LLMClient | None = None,
) -> RunSummary:
    """Run one backtest over [start, end] and record it; return the portfolio-vs-SPY summary."""
    provider = provider or build_data_provider(settings)
    # The rebalancer's universe is its fixed ETF basket; default to it when none is given.
    if universe is None and settings.strategy_mode == "rebalance":
        universe = settings.rebalance.universe()
    engine = build_engine(settings, provider=provider, llm_client=llm_client)
    result = engine.run(start, end, universe=universe)
    return record_run(result, provider, settings, run_id=run_id, store=store)
