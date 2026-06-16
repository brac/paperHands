"""Multi-window evaluation: run the same strategy across regimes, aggregate vs SPY.

The artifact that answers Phase 1's question without cherry-picking a single window. One bad
window never aborts the sweep — it's captured as a failed outcome and the rest continue.

    python -m runner.evaluate [--universe AAPL,MSFT] [--mode rules-only|llm] [--windows w.json]
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.config import Settings, load_settings
from data import build_data_provider
from data.base import DataProvider
from record import BacktestStore
from record.summary import RunSummary
from runner.config import _parse_universe
from runner.run import run_backtest
from runner.windows import DEFAULT_WINDOWS, Window
from screen import build_universe_provider


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    window: Window
    summary: RunSummary | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.summary is not None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    outcomes: tuple[WindowOutcome, ...]
    strategy_mode: str
    universe_size: int

    def successful(self) -> list[WindowOutcome]:
        return [o for o in self.outcomes if o.ok]


def evaluate(
    settings: Settings,
    windows: Sequence[Window] = DEFAULT_WINDOWS,
    universe: Sequence[str] | None = None,
    *,
    provider: DataProvider | None = None,
    store: BacktestStore | None = None,
) -> EvaluationResult:
    """Run each window (fresh broker, shared provider) and collect portfolio-vs-SPY outcomes."""
    provider = provider or build_data_provider(settings)
    store = store or BacktestStore(settings.record.db_path)
    uni = tuple(universe) if universe is not None else None
    size = len(uni) if uni is not None else len(build_universe_provider(settings).symbols())

    outcomes: list[WindowOutcome] = []
    for window in windows:
        try:
            summary = run_backtest(
                settings, window.start, window.end, uni,
                provider=provider, store=store, run_id=window.label,
            )
            outcomes.append(WindowOutcome(window, summary=summary))
        except Exception as exc:  # noqa: BLE001 - one window's failure must not abort the sweep
            outcomes.append(WindowOutcome(window, error=f"{type(exc).__name__}: {exc}"))

    return EvaluationResult(tuple(outcomes), settings.strategy_mode, size)


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def format_evaluation(result: EvaluationResult) -> str:
    """Render the multi-window comparison table + an aggregate verdict line."""
    lines = [
        "PaperHands multi-window evaluation",
        f"mode {result.strategy_mode}  |  universe {result.universe_size} symbols",
        "",
        f"{'Window':18}{'Regime':16}{'Portfolio':>12}{'SPY':>12}{'Excess':>12}",
    ]
    for o in result.outcomes:
        if o.summary is not None:
            s = o.summary
            lines.append(
                f"{o.window.label:18}{o.window.regime:16}"
                f"{_pct(s.portfolio_stats.total_return):>12}"
                f"{_pct(s.benchmark_stats.total_return):>12}"
                f"{_pct(s.excess_return):>12}"
            )
        else:
            failed = f"FAILED: {o.error or ''}"
            lines.append(f"{o.window.label:18}{o.window.regime:16}{failed:>36}")

    summaries = [o.summary for o in result.outcomes if o.summary is not None]
    lines.append("-" * 70)
    if summaries:
        mean_excess = statistics.fmean(s.excess_return for s in summaries)
        beat = sum(s.excess_return > 0 for s in summaries)
        mean_sharpe = statistics.fmean(s.portfolio_stats.sharpe for s in summaries)
        lines.append(
            f"Aggregate: mean excess {_pct(mean_excess)}  |  beat SPY in {beat}/{len(summaries)} "
            f"windows  |  mean Sharpe {mean_sharpe:.2f}"
        )
    else:
        lines.append("Aggregate: no successful windows.")
    return "\n".join(lines)


def _load_windows(path: str) -> tuple[Window, ...]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(
        Window(r["label"], date.fromisoformat(r["start"]), date.fromisoformat(r["end"]),
               r.get("regime", ""))
        for r in rows
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="runner.evaluate")
    parser.add_argument("--universe", help="comma-separated symbols (default: full seed)")
    parser.add_argument("--mode", choices=["rules-only", "llm"], help="override strategy mode")
    parser.add_argument("--windows", help="JSON file of custom windows (default: DEFAULT_WINDOWS)")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.mode:
        settings = settings.model_copy(update={"strategy_mode": args.mode})
    windows = _load_windows(args.windows) if args.windows else DEFAULT_WINDOWS

    result = evaluate(settings, windows, _parse_universe(args.universe))
    print(format_evaluation(result))
    return 0 if result.successful() else 1


if __name__ == "__main__":
    sys.exit(main())
