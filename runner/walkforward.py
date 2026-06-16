"""Walk-forward validation: roll a fixed strategy across many out-of-sample windows.

The fixed four `DEFAULT_WINDOWS` in `runner.evaluate` answer "did it work in these regimes?".
Walk-forward asks the harder question: "is the edge *consistent* out-of-sample?" It slices the
history into a sequence of rolling test windows, runs the (unchanged) strategy on each, and
reports the *distribution* of portfolio-vs-SPY excess — mean/median, how often it beats SPY,
the worst window — instead of one cherry-picked number. The config is fixed across windows (no
parameter fitting), so this measures stability, not in-sample curve-fit.

    python -m runner.walkforward [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                 [--test-days N] [--step-days N] [--universe ...] [--mode ...]
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Sequence
from datetime import date, timedelta

from core.config import Settings, load_settings
from data.base import DataProvider
from record import BacktestStore
from runner.config import _parse_universe
from runner.evaluate import EvaluationResult, evaluate
from runner.windows import Window

# ~6-month test windows, stepped so they don't overlap (independent OOS samples).
DEFAULT_TEST_DAYS = 126
DEFAULT_STEP_DAYS = 126
DEFAULT_START = date(2018, 1, 1)
DEFAULT_END = date(2024, 12, 31)


def rolling_windows(
    start: date, end: date, *, test_days: int, step_days: int
) -> tuple[Window, ...]:
    """Rolling test windows of ``test_days`` stepped by ``step_days`` across ``[start, end]``.

    Each window is ``[w_start, w_start + test_days]`` and is emitted only if it fits entirely
    within ``end`` (no partial window past the range). Labeled ``wf-001…`` with regime ``oos``.
    """
    if test_days <= 0 or step_days <= 0:
        raise ValueError("test_days and step_days must be positive")
    windows: list[Window] = []
    w_start = start
    while w_start + timedelta(days=test_days) <= end:
        w_end = w_start + timedelta(days=test_days)
        windows.append(Window(f"wf-{len(windows) + 1:03d}", w_start, w_end, "oos"))
        w_start = w_start + timedelta(days=step_days)
    return tuple(windows)


def walk_forward(
    settings: Settings,
    start: date,
    end: date,
    *,
    test_days: int = DEFAULT_TEST_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    universe: Sequence[str] | None = None,
    provider: DataProvider | None = None,
    store: BacktestStore | None = None,
) -> EvaluationResult:
    """Run the fixed strategy over the rolling windows and collect per-window OOS outcomes."""
    windows = rolling_windows(start, end, test_days=test_days, step_days=step_days)
    return evaluate(settings, windows, universe, provider=provider, store=store)


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def format_walkforward(result: EvaluationResult) -> str:
    """Render the per-window table + the out-of-sample distribution and a verdict line."""
    lines = [
        "PaperHands walk-forward validation",
        f"mode {result.strategy_mode}  |  universe {result.universe_size} symbols  |  "
        f"{len(result.outcomes)} window(s)",
        "",
        f"{'Window':10}{'Start':12}{'End':12}{'Excess':>12}{'Sharpe':>10}",
    ]
    for o in result.outcomes:
        if o.summary is not None:
            s = o.summary
            lines.append(
                f"{o.window.label:10}{o.window.start.isoformat():12}{o.window.end.isoformat():12}"
                f"{_pct(s.excess_return):>12}{s.portfolio_stats.sharpe:>10.2f}"
            )
        else:
            lines.append(
                f"{o.window.label:10}{o.window.start.isoformat():12}{o.window.end.isoformat():12}"
                f"{'FAILED':>22}"
            )

    summaries = [o.summary for o in result.outcomes if o.summary is not None]
    lines.append("-" * 56)
    if not summaries:
        lines.append("Out-of-sample: no successful windows.")
        return "\n".join(lines)

    excess = [s.excess_return for s in summaries]
    sharpes = [s.portfolio_stats.sharpe for s in summaries]
    drawdowns = [s.portfolio_stats.max_drawdown for s in summaries]
    n = len(summaries)
    median_excess = statistics.median(excess)
    hit_rate = sum(e > 0.0 for e in excess) / n
    worst = min(result.successful(), key=lambda o: o.summary.excess_return)  # type: ignore[union-attr]
    best = max(result.successful(), key=lambda o: o.summary.excess_return)  # type: ignore[union-attr]

    lines.extend([
        f"Out-of-sample over {n} window(s):",
        f"  Excess vs SPY: mean {_pct(statistics.fmean(excess))}  median {_pct(median_excess)}"
        f"  stdev {_pct(statistics.pstdev(excess))}",
        f"  Beat SPY:      {sum(e > 0.0 for e in excess)}/{n} ({hit_rate * 100:.0f}%)",
        f"  Sharpe:        mean {statistics.fmean(sharpes):.2f}  median "
        f"{statistics.median(sharpes):.2f}",
        f"  Max drawdown:  mean {_pct(statistics.fmean(drawdowns))}",
        f"  Worst {worst.window.label} {_pct(worst.summary.excess_return)}  |  "  # type: ignore[union-attr]
        f"Best {best.window.label} {_pct(best.summary.excess_return)}",  # type: ignore[union-attr]
        "",
        "Verdict: "
        + (
            "edge holds out-of-sample"
            if median_excess > 0.0 and hit_rate >= 0.5
            else "no consistent out-of-sample edge"
        ),
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="runner.walkforward")
    parser.add_argument("--start", help=f"YYYY-MM-DD (default {DEFAULT_START.isoformat()})")
    parser.add_argument("--end", help=f"YYYY-MM-DD (default {DEFAULT_END.isoformat()})")
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS,
                        help=f"test-window length in days (default {DEFAULT_TEST_DAYS})")
    parser.add_argument("--step-days", type=int, default=DEFAULT_STEP_DAYS,
                        help=f"days between window starts (default {DEFAULT_STEP_DAYS})")
    parser.add_argument("--universe", help="comma-separated symbols (default: full seed)")
    parser.add_argument("--mode", choices=["rules-only", "llm"], help="override strategy mode")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.mode:
        settings = settings.model_copy(update={"strategy_mode": args.mode})
    start = date.fromisoformat(args.start) if args.start else DEFAULT_START
    end = date.fromisoformat(args.end) if args.end else DEFAULT_END

    result = walk_forward(
        settings, start, end,
        test_days=args.test_days, step_days=args.step_days,
        universe=_parse_universe(args.universe),
    )
    print(format_walkforward(result))
    return 0 if result.successful() else 1


if __name__ == "__main__":
    sys.exit(main())
