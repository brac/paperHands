"""Smoke entrypoint.

Loads config, configures logging, exercises the data provider (a real Tiingo fetch when a
key is configured, else a graceful skip), exercises the sovereign risk gate on a tiny
in-memory example, and exits 0.

    python -m runner.smoke
"""

from __future__ import annotations

import logging
import sys
from datetime import date

from core.config import Settings, load_settings
from core.contracts import (
    AccountState,
    MarketContext,
    Position,
    ProposedOrder,
    ProposedPlan,
)
from core.logging import configure_logging, get_logger
from data import build_data_provider
from data.base import DataProvider
from ingest import build_snapshot_assembler
from risk import apply_risk_gate


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.smoke")

    log.info(
        "config loaded | strategy_mode=%s data_provider=%s max_position_pct=%s",
        settings.strategy_mode,
        settings.data.provider,
        settings.risk.max_position_pct,
    )

    provider = build_data_provider(settings)
    _smoke_data_provider(provider, settings, log)
    _smoke_ingest(provider, settings, log)

    # Tiny end-to-end exercise of the sovereign gate: a sane buy, an oversized buy that
    # must be clamped, and a junk order that must be rejected.
    account = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0,
                           positions=(Position("SPY", qty=1.0, avg_price=400.0),))
    market = MarketContext(
        prices={"AAPL": 200.0, "MSFT": 300.0},
        avg_dollar_volume={"AAPL": 5e9, "MSFT": 4e9},
    )
    plan = ProposedPlan(orders=(
        ProposedOrder("buy", "AAPL", target_weight=0.10, conviction=0.6, reason="trend"),
        ProposedOrder("buy", "MSFT", target_weight=0.99, conviction=0.9, reason="oversized"),
        ProposedOrder("buy", "ZZZZ", target_weight=0.10, conviction=0.5, reason="unknown"),
    ))

    gated = apply_risk_gate(plan, account, market, settings.risk)
    log.info("risk gate | approved=%d rejected=%d", len(gated.orders), len(gated.rejected))
    for order in gated.orders:
        log.info("  approved %s %s qty=%.4f @ %.2f",
                 order.side, order.symbol, order.qty, order.est_price)
    for proposed, reason in gated.rejected:
        log.info("  rejected %s %s -> %s", proposed.action, proposed.symbol, reason)

    log.info("smoke OK")
    return 0


def _smoke_data_provider(
    provider: DataProvider, settings: Settings, log: logging.Logger
) -> None:
    """Fetch one symbol when a Tiingo key is configured; otherwise log a graceful skip."""
    if not settings.tiingo_api_key:
        log.info("data provider | no TIINGO_API_KEY set - skipping live fetch")
        return
    symbol = "AAPL"
    bars = provider.get_daily_bars(symbol, date(2024, 1, 1), date(2024, 3, 31))
    if len(bars):
        log.info(
            "data provider | %s: %d bars %s..%s",
            symbol, len(bars), bars.index.min().date(), bars.index.max().date(),
        )
    else:
        log.info("data provider | %s: no bars returned for range", symbol)


def _smoke_ingest(
    provider: DataProvider, settings: Settings, log: logging.Logger
) -> None:
    """Assemble a point-in-time snapshot when a key is configured; otherwise skip."""
    if not settings.tiingo_api_key:
        log.info("ingest | no TIINGO_API_KEY set - skipping snapshot")
        return
    assembler = build_snapshot_assembler(settings, provider)
    account = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)
    snapshot = assembler.assemble(["AAPL"], date(2024, 3, 28), account)
    log.info("ingest | snapshot %s", snapshot.summary())


if __name__ == "__main__":
    sys.exit(main())
