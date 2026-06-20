"""Daily scheduler — run one ``runner.cycle`` per trading session, gated by Alpaca's clock.

    python -m runner.schedule [--mode ...] [--universe ...] [--max-cycles N]

The Phase-2 spec (§8) asks for the one-shot cycle on a cadence: once per US trading day, after
the open, skipping holidays/weekends. Alpaca's market clock (``broker.market_clock()``) is the
authoritative source of "is the market open / when does it next open", so the loop needs no
separate holiday calendar. One cycle runs per open session; the open-orders guard in
``run_cycle`` keeps a re-run from stacking duplicate orders.

Every time/IO seam (clock, sleep, now, the cycle call, the cycle cap) is injectable so tests
drive many iterations with no real sleeping and no network. An external cron calling
``python -m runner.cycle`` once a day remains a valid alternative to running this loop.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from broker import MarketClock, build_alpaca_broker
from core.config import Settings, load_settings
from core.logging import configure_logging, get_logger
from runner.cycle import run_cycle
from strategy import LLMClient, build_anthropic_client

_log = get_logger("runner.schedule")


def _seconds_until(target: datetime, now: datetime) -> float:
    """Non-negative seconds from ``now`` to ``target`` (0 if already past)."""
    return max((target - now).total_seconds(), 0.0)


def run_scheduler(
    settings: Settings,
    *,
    broker: Any | None = None,
    max_cycles: int | None = None,
    clock_fn: Callable[[], MarketClock] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] | None = None,
    cycle_fn: Callable[[], object] | None = None,
) -> int:
    """Loop forever (or until ``max_cycles``), running one cycle per open trading session.

    Returns the number of cycles run. ``broker``/``clock_fn``/``cycle_fn`` default to the real
    Alpaca-backed implementations; tests inject stubs. A cycle that raises is logged and the loop
    continues — one bad session must not kill the schedule.
    """
    broker = broker or build_alpaca_broker(settings)
    clock_fn = clock_fn or broker.market_clock
    cycle_fn = cycle_fn or (lambda: run_cycle(settings, broker=broker))
    now_fn = now_fn or (lambda: datetime.now(UTC))
    poll = settings.schedule.poll_seconds
    offset = timedelta(minutes=settings.schedule.open_offset_minutes)

    count = 0
    ran_session: object = None
    while max_cycles is None or count < max_cycles:
        clock = clock_fn()
        if clock.is_open:
            session = clock.next_close.date()  # the session currently open closes today
            if session != ran_session:
                _log.info("market open | running cycle for session %s", session)
                try:
                    cycle_fn()
                except Exception as exc:  # noqa: BLE001 - never let one session kill the loop
                    _log.error("scheduled cycle failed: %s: %s", type(exc).__name__, exc)
                ran_session = session
                count += 1
                continue
            # Already ran this session — idle until it closes, then wait for the next open.
            sleep_fn(min(_seconds_until(clock.next_close, now_fn()), poll))
        else:
            target = clock.next_open + offset
            _log.info("market closed | next open %s; waiting", clock.next_open)
            sleep_fn(min(_seconds_until(target, now_fn()), poll))
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.schedule", description="Run one cycle per trading session (Alpaca clock)."
    )
    parser.add_argument(
        "--mode", choices=["rules-only", "llm", "rebalance"], help="override strategy mode")
    parser.add_argument("--universe", help="comma-separated symbols (default: full seed)")
    parser.add_argument("--max-cycles", type=int, help="stop after N cycles (default: run forever)")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.schedule")

    if args.mode:
        settings = settings.model_copy(update={"strategy_mode": args.mode})

    universe = (
        tuple(s.strip().upper() for s in args.universe.split(",") if s.strip())
        if args.universe
        else None
    )

    try:
        broker = build_alpaca_broker(settings)
        llm_client: LLMClient | None = (
            build_anthropic_client(settings) if settings.strategy_mode == "llm" else None
        )
    except RuntimeError as exc:
        log.error("cannot start scheduler: %s", exc)
        return 2

    def cycle_fn() -> object:
        return run_cycle(settings, universe=universe, llm_client=llm_client, broker=broker)

    n = run_scheduler(settings, broker=broker, max_cycles=args.max_cycles, cycle_fn=cycle_fn)
    log.info("scheduler stopped | cycles run=%d", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
