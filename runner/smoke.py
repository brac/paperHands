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

import pandas as pd

from core.config import ScreenConfig, Settings, load_settings
from core.contracts import (
    AccountState,
    MarketContext,
    NewsContext,
    Position,
    ProposedOrder,
    ProposedPlan,
    SymbolMetadata,
)
from core.logging import configure_logging, get_logger
from data import build_data_provider
from data.base import DataProvider
from data.frame import COLUMNS, INDEX_NAME
from ingest import build_snapshot_assembler
from ingest.snapshot import MarketSnapshot
from risk import apply_risk_gate
from screen import screen


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
    _smoke_screen(log)

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


def _synthetic_bars(latest_close: float, daily_volume: float, momentum: float) -> pd.DataFrame:
    """A 100-bar frame with a flat raw close (for liquidity/min-price) and an adj_close that
    rises by ``momentum`` over the window (for the ROC score)."""
    n = 100
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n), name=INDEX_NAME)
    start_adj = latest_close / (1.0 + momentum)
    adj = [start_adj + (latest_close - start_adj) * (i / (n - 1)) for i in range(n)]
    data = {c: [latest_close] * n for c in COLUMNS}
    data["volume"] = [daily_volume] * n
    data["adj_close"] = adj
    return pd.DataFrame(data, index=idx)


def _smoke_screen(log: logging.Logger) -> None:
    """Run the pure screen over a tiny synthetic snapshot. Offline; always runs."""
    prices = {
        "AAA": _synthetic_bars(latest_close=150.0, daily_volume=1_000_000, momentum=0.30),
        "BBB": _synthetic_bars(latest_close=80.0, daily_volume=2_000_000, momentum=0.10),
        "CCC": _synthetic_bars(latest_close=40.0, daily_volume=500_000, momentum=0.50),
        "PENNY": _synthetic_bars(latest_close=2.0, daily_volume=5_000_000, momentum=0.90),
    }
    metadata = {
        "AAA": SymbolMetadata("AAA", "Alpha Co", "Information Technology"),
        "BBB": SymbolMetadata("BBB", "Beta Co", "Health Care"),
        "CCC": SymbolMetadata("CCC", "Gamma Co", "Energy"),
        "PENNY": SymbolMetadata("PENNY", "Penny Co", "Energy"),
    }
    news = {"BBB": NewsContext(sentiment=0.5, headline_count=3)}
    account = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)
    snapshot = MarketSnapshot(
        as_of=date(2024, 5, 20), prices=prices, account=account, news=news
    )

    result = screen(snapshot, metadata, ScreenConfig())
    log.info(
        "screen | candidates=%d dropped=%d", len(result.candidates), len(result.dropped)
    )
    for cand in result.candidates:
        log.info("  #%d %s score=%.4f [%s]", cand.rank, cand.symbol, cand.score, cand.sector)
    for symbol, reason in result.dropped:
        log.info("  dropped %s -> %s", symbol, reason)


if __name__ == "__main__":
    sys.exit(main())
