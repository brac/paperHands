"""Read-side CLI for the live paper-trading loop — print the latest cycle + running summary.

    python -m runner.report

Opens the ``CycleStore`` at ``settings.record.db_path``, prints the most recent cycle and a
running portfolio-vs-SPY summary across all recorded cycles, and exits 0. This is the §7
"benchmark or it didn't happen" read-side; it never writes and never calls the network.
"""

from __future__ import annotations

import argparse
import sys

from core.config import load_settings
from core.logging import configure_logging, get_logger
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.report", description="Print the latest cycle and the running SPY summary."
    )
    parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.report")

    store = CycleStore(settings.record.db_path)
    latest = store.latest_cycle()
    if latest is None:
        log.info("no cycles recorded yet.")
        print("no cycles recorded yet.")
        return 0

    cycles = [(c.as_of, c.equity) for c in store.list_cycles()]
    summary = summarize_cycles(cycles, settings.broker.starting_cash)
    print(format_cycle_report(latest, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
