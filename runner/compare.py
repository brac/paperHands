"""Run the baseline sleeve and the YOLO sleeve over one window, export the combined dashboard.

The driver behind the third line on the graph. It runs two backtests over the *same* window
sharing one data provider (warm cache, identical SPY benchmark + trading calendar): the baseline
(the rebalancer, by default) and the max-risk ``yolo`` sleeve. Both land in the store under fixed
run ids, then ``dashboard.export.build_export`` overlays the YOLO equity curve onto the baseline
document as ``yolo_equity`` and writes the static JSON the SPA reads.

    python -m runner.compare --start 2023-01-01 --end 2023-12-31
                             [--baseline-mode rebalance] [--yolo-universe GME,AMC,TSLA]
                             [--db results.sqlite] [--out dashboard/web/public/data.json]

Honest framing (unchanged from the rest of the project): the YOLO line trades a point-in-time
price/volume *proxy* for hype until a real social feed is wired, so it is labeled "proxy hype"
and is paper-only — it is a contrast, not a recommendation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from core.config import Settings, load_settings
from core.logging import configure_logging, get_logger
from dashboard.export import build_export
from data import build_data_provider
from data.base import DataProvider
from record import BacktestStore, RunSummary
from runner.config import _parse_universe
from runner.run import run_backtest

_BASELINE_RUN_ID = "compare-baseline"
_YOLO_RUN_ID = "compare-yolo"


def run_comparison(
    settings: Settings,
    start: date,
    end: date,
    *,
    baseline_mode: str = "rebalance",
    yolo_universe: tuple[str, ...] | None = None,
    provider: DataProvider | None = None,
    store: BacktestStore | None = None,
) -> tuple[RunSummary, RunSummary]:
    """Run the baseline + YOLO backtests over the same window; return (baseline, yolo) summaries.

    The baseline uses its mode's natural universe (the rebalancer's ETF basket); the YOLO sleeve
    ranks ``yolo_universe`` — or the full seed universe when none is given — by its hype proxy
    each cycle. The shared provider keeps the SPY benchmark and calendar identical across both.
    """
    provider = provider or build_data_provider(settings)
    store = store or BacktestStore(settings.record.db_path)

    base_settings = settings.model_copy(update={"strategy_mode": baseline_mode})
    baseline = run_backtest(
        base_settings, start, end, None,
        provider=provider, store=store, run_id=_BASELINE_RUN_ID,
    )

    yolo_settings = settings.model_copy(update={"strategy_mode": "yolo"})
    yolo = run_backtest(
        yolo_settings, start, end, yolo_universe,
        provider=provider, store=store, run_id=_YOLO_RUN_ID,
    )
    return baseline, yolo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.compare",
        description="Run baseline + YOLO over one window and export the combined dashboard JSON.",
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--baseline-mode", default="rebalance",
        choices=["rules-only", "llm", "rebalance"],
        help="strategy mode for the baseline line (default: rebalance)")
    parser.add_argument(
        "--yolo-universe",
        help="comma-separated symbols the YOLO sleeve ranks (default: the full seed universe)")
    parser.add_argument("--db", help="path to the results SQLite db (default: from config)")
    parser.add_argument(
        "--out", default="dashboard/web/public/data.json",
        help="output JSON path (default: dashboard/web/public/data.json)")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.compare")
    db_path = args.db or settings.record.db_path

    try:
        baseline, yolo = run_comparison(
            settings,
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            baseline_mode=args.baseline_mode,
            yolo_universe=_parse_universe(args.yolo_universe),
            store=BacktestStore(db_path),
        )
    except Exception as exc:  # noqa: BLE001 - report cleanly, never a traceback dump
        log.error("comparison failed: %s: %s", type(exc).__name__, exc)
        return 1

    document = build_export(db_path, _BASELINE_RUN_ID, settings, yolo_run_id=_YOLO_RUN_ID)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    log.info(
        "baseline %s final $%.0f | YOLO final $%.0f | SPY final $%.0f",
        args.baseline_mode, baseline.portfolio_final, yolo.portfolio_final,
        baseline.benchmark_final,
    )
    print(f"wrote {out_path} (baseline {_BASELINE_RUN_ID} + yolo {_YOLO_RUN_ID})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
