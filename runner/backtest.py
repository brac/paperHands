"""Run a single backtest and print the portfolio-vs-SPY report.

    python -m runner.backtest --start 2024-01-01 --end 2024-03-31 [--universe AAPL,MSFT]
                              [--mode rules-only|llm] [--config run.json]
"""

from __future__ import annotations

import argparse
import sys

from core.config import load_settings
from core.logging import configure_logging, get_logger
from record import format_report
from runner.config import resolve_run_config
from runner.run import run_backtest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner.backtest")
    parser.add_argument("--start", help="YYYY-MM-DD (or via --config)")
    parser.add_argument("--end", help="YYYY-MM-DD (or via --config)")
    parser.add_argument("--universe", help="comma-separated symbols (default: full seed)")
    parser.add_argument("--mode", choices=["rules-only", "llm"], help="override strategy mode")
    parser.add_argument("--config", help="JSON run-config; CLI flags override it")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.backtest")

    try:
        cfg = resolve_run_config(
            config_path=args.config, start=args.start, end=args.end,
            universe=args.universe, mode=args.mode, label=None,
        )
    except (ValueError, OSError) as exc:
        log.error("invalid run config: %s", exc)
        return 2

    if cfg.mode:
        settings = settings.model_copy(update={"strategy_mode": cfg.mode})

    try:
        summary = run_backtest(settings, cfg.start, cfg.end, cfg.universe)
    except Exception as exc:  # noqa: BLE001 - report the failure cleanly, never a traceback dump
        log.error("backtest failed: %s: %s", type(exc).__name__, exc)
        return 1

    print(format_report(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
