"""Smoke entrypoint for the scaffold + risk-gate slice.

Loads config, configures logging, exercises the sovereign risk gate on a tiny in-memory
example (no network, no provider yet), and exits 0. The fuller spec smoke that *fetches a
symbol's history* arrives with the Data Provider slice, since no provider exists yet.

    python -m runner.smoke
"""

from __future__ import annotations

import sys

from core.config import load_settings
from core.contracts import (
    AccountState,
    MarketContext,
    Position,
    ProposedOrder,
    ProposedPlan,
)
from core.logging import configure_logging, get_logger
from risk import apply_risk_gate


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    log = get_logger("runner.smoke")

    log.info(
        "config loaded | strategy_mode=%s max_position_pct=%s max_positions=%s",
        settings.strategy_mode,
        settings.risk.max_position_pct,
        settings.risk.max_positions,
    )

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


if __name__ == "__main__":
    sys.exit(main())
