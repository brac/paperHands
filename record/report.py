"""Render a run summary as a portfolio-vs-SPY text report, and a CLI to print from the store.

    python -m record.report [--run-id ID | --latest]
"""

from __future__ import annotations

import argparse
import sys

from record.store import BacktestStore
from record.summary import RunSummary


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def format_report(summary: RunSummary) -> str:
    """A portfolio-vs-SPY text table with headline stats."""
    p, b = summary.portfolio_stats, summary.benchmark_stats
    rows = [
        ("Final equity", _money(summary.portfolio_final), _money(summary.benchmark_final)),
        ("Total return", _pct(p.total_return), _pct(b.total_return)),
        ("CAGR", _pct(p.cagr), _pct(b.cagr)),
        ("Max drawdown", _pct(p.max_drawdown), _pct(b.max_drawdown)),
        ("Volatility", _pct(p.volatility), _pct(b.volatility)),
        ("Sharpe-ish", f"{p.sharpe:.2f}", f"{b.sharpe:.2f}"),
        ("Hit rate", f"{p.hit_rate * 100:.1f}%", f"{b.hit_rate * 100:.1f}%"),
        ("Turnover", f"{p.turnover:.2f}x", "-"),
    ]
    lines = [
        "PaperHands backtest report",
        f"Run {summary.run_id}  |  {summary.start} .. {summary.end}  |  "
        f"capital {_money(summary.starting_cash)}  |  mode {summary.strategy_mode}",
        "",
        f"{'':16}{'Portfolio':>16}{'SPY (hold)':>16}",
    ]
    lines += [f"{label:16}{port:>16}{bench:>16}" for label, port, bench in rows]
    lines += [
        "",
        f"Excess vs SPY: {_pct(summary.excess_return)}   "
        f"(steps {summary.n_steps}, fills {summary.n_fills})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record.report", description="Print a backtest report.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", help="report a specific run id")
    group.add_argument("--latest", action="store_true", help="report the most recent run")
    args = parser.parse_args(argv)

    from core.config import load_settings

    store = BacktestStore(load_settings().record.db_path)
    run_id = args.run_id or store.latest_run_id()
    if run_id is None:
        print("no runs recorded yet.")
        return 1
    print(format_report(store.load_summary(run_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
