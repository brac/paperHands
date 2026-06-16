"""One-shot live paper-trading cycle — the Phase-2 live-ingest glue + 7-stage pipeline.

Runs the exact Phase-1 pure pipeline ONCE for a single ``as_of``, but against a *real* Alpaca
paper account instead of the SimulatedBroker: the only thing that changes from the backtest is
where ``account_state`` and ``submit`` come from. Stage order mirrors ``engine.engine`` exactly
(snapshot -> screen -> signals -> strategy -> market -> gate -> submit), minus the simulator's
fill/mark ticks (the live broker settles those itself).

Safety doctrine: every stage runs *before* any order is submitted, and any stage exception
aborts the cycle cleanly with a log line — we never round-trip a partial or unsafe plan to the
broker. ``dry_run`` computes and records the gated plan but skips ``submit`` entirely. Broker
and data provider are injected (defaulting to the real factories) so unit tests drive the whole
cycle with stubs — no network, no installed Alpaca SDK. Mirrors ``runner.run``'s injection seam.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date

from broker import Broker, build_alpaca_broker
from core.config import Settings, load_settings
from core.logging import configure_logging, get_logger
from data import build_data_provider
from data.base import DataProvider
from engine.market import build_market_context
from ingest import build_snapshot_assembler
from record.cycle_store import CycleStore
from risk import apply_risk_gate
from screen import build_universe_provider, screen
from signals import compute_signals
from strategy import LLMClient, build_anthropic_client, build_strategy_context, propose_plan

_log = get_logger("runner.cycle")


def run_cycle(
    settings: Settings,
    *,
    as_of: date | None = None,
    universe: Sequence[str] | None = None,
    dry_run: bool = False,
    llm_client: LLMClient | None = None,
    broker: Broker | None = None,
    provider: DataProvider | None = None,
    store: CycleStore | None = None,
) -> str:
    """Run stages 1-7 once for ``as_of`` against the live paper account; return the cycle id.

    ``broker`` / ``provider`` / ``store`` default to the real factories but are injectable so
    tests drive the cycle with stubs (no network, no Alpaca SDK). History is capped at ``as_of``
    by the assembler, so passing an ``as_of`` introduces no look-ahead. Any stage exception is
    logged and re-raised *before* submit, so an unsafe/partial plan can never reach the broker.
    """
    as_of = as_of or date.today()
    provider = provider or build_data_provider(settings)
    broker = broker or build_alpaca_broker(settings)
    assembler = build_snapshot_assembler(settings, provider)
    universe_provider = build_universe_provider(settings)

    symbols = tuple(universe) if universe is not None else universe_provider.symbols()
    metadata = universe_provider.metadata_for(symbols)
    _log.info("cycle start | as_of=%s symbols=%d dry_run=%s", as_of, len(symbols), dry_run)

    try:
        # 1. Live-ingest glue: pull the REAL paper account, then assemble the snapshot verbatim.
        account = broker.account_state()
        _log.info(
            "account | equity=%.2f cash=%.2f buying_power=%.2f positions=%d",
            account.equity, account.cash, account.buying_power, len(account.positions),
        )

        # 2. Point-in-time snapshot (history capped at as_of).
        snapshot = assembler.assemble(symbols, as_of, account)
        _log.info("snapshot | %s", snapshot.summary())

        # 3. Screen -> ranked candidates.
        candidates = tuple(
            c.symbol for c in screen(snapshot, metadata, settings.screen).candidates
        )
        _log.info("screen | candidates=%d", len(candidates))

        # 4. Signals over the candidates.
        signals = compute_signals(snapshot, candidates, settings.signals)
        _log.info("signals | computed=%d", len(signals))

        # 5. Strategy proposal (mode from settings; llm client injected only in llm mode).
        ctx = build_strategy_context(settings, llm_client)
        proposed = propose_plan(signals, account.positions, account.cash, ctx)
        _log.info("strategy (%s) | proposed=%d", ctx.mode, len(proposed.orders))

        # 6. Sovereign risk gate over the proposal.
        held = tuple(p.symbol for p in account.positions)
        ctx_symbols = tuple(dict.fromkeys(candidates + held))
        market = build_market_context(snapshot, ctx_symbols, settings.engine.adv_window)
        gated = apply_risk_gate(proposed, account, market, settings.risk)
        _log.info("risk gate | approved=%d rejected=%d", len(gated.orders), len(gated.rejected))
    except Exception as exc:
        # Clean failure: abort before any submit; never leave a partial/unsafe order.
        _log.error("cycle aborted before submit | %s: %s", type(exc).__name__, exc)
        raise

    # 7. Submit (skipped on dry_run) — never submit on a failed/empty plan.
    if dry_run:
        _log.info("submit | dry_run -> skipping broker submit")
    elif gated.orders:
        broker.submit(gated)
        _log.info("submit | sent %d order(s) to broker", len(gated.orders))
    else:
        _log.info("submit | no approved orders -> nothing to submit")

    # Always record the cycle (honest: benchmark_equity left null this slice — see read-side).
    store = store or CycleStore(settings.record.db_path)
    cycle_id = store.save_cycle(
        as_of=as_of,
        strategy_mode=ctx.mode,
        snapshot_summary=snapshot.summary(),
        proposed=proposed,
        gated=gated,
        equity=account.equity,
        cash=account.cash,
        benchmark_equity=None,
    )
    _log.info("cycle recorded | cycle_id=%s", cycle_id)
    return cycle_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runner.cycle", description="Run one live paper-trading cycle and record it."
    )
    parser.add_argument("--mode", choices=["rules-only", "llm"], help="override strategy mode")
    parser.add_argument("--universe", help="comma-separated symbols (default: full seed)")
    parser.add_argument("--dry-run", action="store_true", help="compute + record but never submit")
    parser.add_argument("--as-of", help="YYYY-MM-DD decision date (default: today)")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.cycle")

    if args.mode:
        settings = settings.model_copy(update={"strategy_mode": args.mode})

    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
    except ValueError as exc:
        log.error("invalid --as-of: %s", exc)
        return 2

    universe = (
        tuple(s.strip().upper() for s in args.universe.split(",") if s.strip())
        if args.universe
        else None
    )

    llm_client: LLMClient | None = None
    if settings.strategy_mode == "llm":
        try:
            llm_client = build_anthropic_client(settings)
        except RuntimeError as exc:
            log.error("cannot build LLM client: %s", exc)
            return 2

    try:
        run_cycle(
            settings, as_of=as_of, universe=universe, dry_run=args.dry_run, llm_client=llm_client
        )
    except Exception as exc:  # noqa: BLE001 - report the failure cleanly, never a traceback dump
        log.error("cycle failed: %s: %s", type(exc).__name__, exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
