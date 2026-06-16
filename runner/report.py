"""Read-side CLI for the live paper-trading loop — print the latest cycle + running summary.

    python -m runner.report

Opens the ``CycleStore`` at ``settings.record.db_path``, prints the most recent cycle and a
running portfolio-vs-SPY summary across all recorded cycles, and exits 0. This is the §7
"benchmark or it didn't happen" read-side; it never writes.

The SPY benchmark is fetched at read time (mirroring ``record/recorder.py``) so the summary is
real, not stubbed. The fetch is best-effort: with no data key or no network it degrades to a
no-benchmark summary rather than failing the report.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date

import pandas as pd

from core.config import Settings, load_settings
from core.logging import configure_logging, get_logger
from data import build_data_provider
from data.base import DataProvider
from record.cycle_store import CycleRecord, CycleStore
from record.cycle_summary import CycleSummary, summarize_cycles


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def format_cycle_report(latest: CycleRecord, summary: CycleSummary) -> str:
    """A compact text view of the latest cycle and the running portfolio-vs-SPY summary."""
    lines = [
        "PaperHands live cycle report",
        f"Latest cycle {latest.cycle_id}  |  as of {latest.as_of}  |  "
        f"mode {latest.strategy_mode}",
        f"  snapshot: {latest.snapshot_summary}",
        f"  equity {_money(latest.equity)}  |  cash {_money(latest.cash)}  |  "
        f"orders {len(latest.gated.orders)} (rejected {len(latest.gated.rejected)})",
        "",
        f"Running summary over {summary.n_cycles} cycle(s):",
        f"  Portfolio return: {_pct(summary.portfolio_return)}",
        f"  SPY return:       {_pct(summary.benchmark_return)}",
        f"  Excess vs SPY:    {_pct(summary.excess)}",
        f"  Latest equity:    {_money(summary.latest_equity)}",
    ]
    return "\n".join(lines)


def _load_spy_bars(
    cycles: Sequence[tuple[date, float]],
    settings: Settings,
    provider: DataProvider | None,
) -> pd.DataFrame | None:
    """Best-effort SPY history over the cycle span; ``None`` (with a log note) if unavailable."""
    log = get_logger("runner.report")
    dates = [d for d, _ in cycles]
    start, end = dates[0], dates[-1]
    try:
        provider = provider or build_data_provider(settings)
        bars = provider.get_daily_bars(
            settings.engine.calendar_symbol, start, end, as_of=end)
    except Exception as exc:  # noqa: BLE001 - the read-side must never fail on a data hiccup
        log.info("benchmark | SPY fetch unavailable (%s) -> SPY column omitted", exc)
        return None
    if bars.empty:
        log.info("benchmark | no SPY bars for %s..%s -> SPY column omitted", start, end)
        return None
    return bars


def build_cycle_report(
    store: CycleStore, settings: Settings, *, provider: DataProvider | None = None
) -> str:
    """Render the latest cycle + running portfolio-vs-SPY summary (SPY fetched at read time)."""
    latest = store.latest_cycle()
    if latest is None:
        return "no cycles recorded yet."
    cycles = [(c.as_of, c.equity) for c in store.list_cycles()]
    spy_bars = _load_spy_bars(cycles, settings, provider)
    summary = summarize_cycles(cycles, settings.broker.starting_cash, spy_bars=spy_bars)
    return format_cycle_report(latest, summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.report", description="Print the latest cycle and the running SPY summary."
    )
    parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)

    store = CycleStore(settings.record.db_path)
    print(build_cycle_report(store, settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
