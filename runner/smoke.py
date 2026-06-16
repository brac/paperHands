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

from broker import SimulatedBroker, build_alpaca_broker
from core.config import (
    BrokerConfig,
    ScreenConfig,
    Settings,
    SignalConfig,
    StrategyConfig,
    load_settings,
)
from core.contracts import (
    AccountState,
    ExecutableOrder,
    ExecutablePlan,
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
from engine import build_engine
from ingest import build_snapshot_assembler
from ingest.snapshot import MarketSnapshot
from record import format_report, record_run
from risk import apply_risk_gate
from screen import screen
from signals import compute_signals
from strategy import StrategyContext, propose_plan


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
    _smoke_signals(log)
    _smoke_strategy(log)
    _smoke_broker(log)
    _smoke_alpaca(settings, log)
    _smoke_engine(settings, log)

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


def _smoke_signals(log: logging.Logger) -> None:
    """Compute indicators over a tiny synthetic snapshot. Offline; always runs."""
    prices = {
        "AAA": _synthetic_bars(latest_close=150.0, daily_volume=1_000_000, momentum=0.30),
        "BBB": _synthetic_bars(latest_close=80.0, daily_volume=2_000_000, momentum=-0.10),
    }
    news = {"AAA": NewsContext(sentiment=0.4, headline_count=2)}
    account = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)
    snapshot = MarketSnapshot(
        as_of=date(2024, 5, 20), prices=prices, account=account, news=news
    )

    signals = compute_signals(snapshot, ["AAA", "BBB"], SignalConfig())
    log.info("signals | computed=%d", len(signals))
    for sym, sig in signals.items():
        log.info(
            "  %s roc=%s rsi=%s trend=%s atr_pct=%s news=%s",
            sym,
            _fmt(sig.roc), _fmt(sig.rsi), _fmt(sig.trend_strength),
            _fmt(sig.atr_pct), _fmt(sig.news_sentiment),
        )


def _smoke_strategy(log: logging.Logger) -> None:
    """Run rules-only propose_plan over a synthetic snapshot. Offline; always runs."""
    prices = {
        "AAA": _synthetic_bars(latest_close=150.0, daily_volume=1_000_000, momentum=0.30),
        "BBB": _synthetic_bars(latest_close=80.0, daily_volume=2_000_000, momentum=-0.10),
    }
    account = AccountState(
        cash=10_000.0, equity=10_000.0, buying_power=10_000.0,
        positions=(Position("BBB", qty=10.0, avg_price=90.0),),
    )
    snapshot = MarketSnapshot(as_of=date(2024, 5, 20), prices=prices, account=account)

    signals = compute_signals(snapshot, ["AAA", "BBB"], SignalConfig())
    ctx = StrategyContext(mode="rules-only", config=StrategyConfig())
    plan = propose_plan(signals, account.positions, account.cash, ctx)
    log.info("strategy (rules-only) | proposed orders=%d", len(plan.orders))
    for order in plan.orders:
        log.info(
            "  %s %s target_weight=%.4f conviction=%.2f (%s)",
            order.action, order.symbol, order.target_weight, order.conviction, order.reason,
        )


def _smoke_broker(log: logging.Logger) -> None:
    """Submit a plan, fill it at the next-bar open with costs, mark to market. Offline."""
    broker = SimulatedBroker(BrokerConfig(starting_cash=10_000.0))
    plan = ExecutablePlan(orders=(ExecutableOrder("AAA", "buy", qty=10.0, est_price=100.0),))
    broker.submit(plan)
    log.info("broker | submitted; pre-fill cash=%.2f positions=%d",
             broker.account_state().cash, len(broker.account_state().positions))
    fills = broker.fill_at_open({"AAA": 100.0})  # next-bar open
    for f in fills:
        log.info("  filled %s %s qty=%.4f @ %.4f (commission=%.2f)",
                 f.side, f.symbol, f.qty, f.price, f.commission)
    broker.mark_to_market(date(2024, 5, 21), {"AAA": 105.0})
    state = broker.account_state()
    log.info("broker | post-fill cash=%.2f equity=%.2f day_pnl=%.2f",
             state.cash, state.equity, state.day_pnl)


def _smoke_alpaca(settings: Settings, log: logging.Logger) -> None:
    """Hit the Alpaca paper account when keys are set; otherwise log a graceful skip.

    Builds the live/paper broker (always paper unless LIVE_TRADING is confirmed) and prints
    equity + buying power. Mirrors the TIINGO-key skip pattern; needs network once when keyed.
    """
    if not (settings.alpaca_api_key and settings.alpaca_secret_key):
        log.info("alpaca | no ALPACA_API_KEY/ALPACA_SECRET_KEY set - skipping paper account")
        return
    broker = build_alpaca_broker(settings)
    account = broker.account_state()
    log.info(
        "alpaca | paper account equity=%.2f buying_power=%.2f positions=%d",
        account.equity, account.buying_power, len(account.positions),
    )


def _smoke_engine(settings: Settings, log: logging.Logger) -> None:
    """Run a short real backtest when a Tiingo key is set; otherwise skip. Needs network once."""
    if not settings.tiingo_api_key:
        log.info("engine | no TIINGO_API_KEY set - skipping backtest")
        return
    provider = build_data_provider(settings)
    engine = build_engine(settings)
    result = engine.run(date(2024, 1, 1), date(2024, 3, 31), universe=["AAPL", "MSFT"])
    log.info(
        "engine | backtest steps=%d bars=%d fills=%d final_equity=%.2f",
        len(result.steps), len(result.equity_curve), len(result.fills), result.final_equity(),
    )
    summary = record_run(result, provider, settings)
    for line in format_report(summary).splitlines():
        log.info("record | %s", line)


def _fmt(value: float | None) -> str:
    return "None" if value is None else f"{value:.4f}"


if __name__ == "__main__":
    sys.exit(main())
