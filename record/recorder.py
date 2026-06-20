"""Orchestrates recording a backtest: benchmark + stats + persistence.

The composition seam that does I/O (fetches SPY via the provider, writes the store), keeping
``stats``/``benchmark`` pure. Consumes the engine's ``BacktestResult`` unchanged.
"""

from __future__ import annotations

from core.config import Settings
from data.base import DataProvider
from engine.result import BacktestResult
from record.benchmark import compute_benchmark
from record.stats import compute_stats
from record.store import BacktestStore, new_run_id
from record.summary import RunSummary


def record_run(
    result: BacktestResult,
    provider: DataProvider,
    settings: Settings,
    *,
    run_id: str | None = None,
    store: BacktestStore | None = None,
) -> RunSummary:
    """Compute the SPY benchmark + stats for ``result``, persist everything, return the summary."""
    run_id = run_id or new_run_id()
    store = store or BacktestStore(settings.record.db_path)

    spy = provider.get_daily_bars(
        settings.engine.calendar_symbol, result.start, result.end, as_of=result.end)
    dates = [p.timestamp for p in result.equity_curve]
    benchmark_curve = compute_benchmark(spy, dates, settings.broker.starting_cash)

    portfolio_equities = [p.equity for p in result.equity_curve]
    benchmark_equities = [e for _, e in benchmark_curve]

    summary = RunSummary(
        run_id=run_id,
        start=result.start,
        end=result.end,
        starting_cash=settings.broker.starting_cash,
        strategy_mode=settings.strategy_mode,
        n_steps=len(result.steps),
        n_fills=len(result.fills),
        portfolio_final=portfolio_equities[-1] if portfolio_equities else 0.0,
        benchmark_final=benchmark_equities[-1] if benchmark_equities else 0.0,
        portfolio_stats=compute_stats(portfolio_equities, result.fills),
        benchmark_stats=compute_stats(benchmark_equities),
    )
    store.save_run(summary, result, benchmark_curve)
    return summary
