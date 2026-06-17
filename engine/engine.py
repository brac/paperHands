"""The event-driven backtest engine — walks the calendar and runs the real pipeline.

A thin hand-rolled loop behind the ``Engine`` Protocol (no backtrader): at each trading day
it fills the prior bar's orders at this bar's open, then — on decision days — assembles the
point-in-time snapshot and runs ingest → screen → signals → strategy → risk gate → broker,
queuing the result for the *next* open. The engine contains no business logic; it only
sequences the existing pure stages with as-of-correct timing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol, runtime_checkable

import pandas as pd

from broker.simulated import SimulatedBroker
from core.config import EngineConfig, RiskParams, ScreenConfig, SignalConfig
from data.base import DataProvider
from engine.market import build_market_context
from engine.result import BacktestResult, StepRecord
from ingest.assembler import SnapshotAssembler
from risk import apply_risk_gate
from screen import screen
from screen.universe import UniverseProvider
from signals import compute_signals
from strategy import compute_market_regime, propose_plan
from strategy.context import StrategyContext


@runtime_checkable
class Engine(Protocol):
    """Drives a backtest over a date range and returns its result."""

    def run(self, start: date, end: date) -> BacktestResult:
        ...


class BacktestEngine:
    """Hand-rolled event-driven backtest over the SimulatedBroker."""

    def __init__(
        self,
        provider: DataProvider,
        assembler: SnapshotAssembler,
        universe_provider: UniverseProvider,
        *,
        screen_config: ScreenConfig,
        signal_config: SignalConfig,
        strategy_ctx: StrategyContext,
        risk_params: RiskParams,
        broker: SimulatedBroker,
        config: EngineConfig,
    ) -> None:
        self._provider = provider
        self._assembler = assembler
        self._universe_provider = universe_provider
        self._screen_config = screen_config
        self._signal_config = signal_config
        self._strategy_ctx = strategy_ctx
        self._risk_params = risk_params
        self._broker = broker
        self._config = config

    def run(
        self, start: date, end: date, universe: Sequence[str] | None = None
    ) -> BacktestResult:
        symbols = (
            tuple(universe) if universe is not None
            else self._universe_provider.symbols_in_window(start, end)
        )
        metadata = self._universe_provider.metadata_for(symbols)

        # The reference frame drives both the trading calendar and the market-regime overlay.
        # Fetch it with pre-window lookback so the regime MA can form on the first decision day;
        # the trading calendar is still only the in-window dates.
        regime_lookback = timedelta(days=self._strategy_ctx.config.regime_ma_window * 2 + 10)
        spy = self._provider.get_daily_bars(
            self._config.calendar_symbol, start - regime_lookback, end, as_of=end)
        calendar = [ts.date() for ts in spy.index if ts.date() >= start]
        frames = {s: self._provider.get_daily_bars(s, start, end, as_of=end) for s in symbols}

        steps: list[StepRecord] = []
        for i, day in enumerate(calendar):
            # 1. Fill orders queued on the previous decision, at this bar's open.
            self._broker.fill_at_open(self._prices_on(frames, symbols, day, "open"))
            self._liquidate_delisted(frames, day)
            account = self._broker.account_state()

            # 2. Decision (as-of-correct), queued for the *next* open.
            if i % self._config.rebalance_every_n_days == 0:
                snapshot = self._assembler.assemble(symbols, day, account)
                candidates = tuple(c.symbol for c in screen(
                    snapshot, metadata, self._screen_config).candidates)
                signals = compute_signals(snapshot, candidates, self._signal_config)
                regime = compute_market_regime(
                    spy.loc[spy.index <= pd.Timestamp(day)],
                    ma_window=self._strategy_ctx.config.regime_ma_window,
                    reference=self._config.calendar_symbol,
                )
                raw = propose_plan(
                    signals, account.positions, account.cash, self._strategy_ctx, regime=regime)
                held = tuple(p.symbol for p in account.positions)
                ctx_symbols = tuple(dict.fromkeys(candidates + held))
                market = build_market_context(snapshot, ctx_symbols, self._config.adv_window)
                gated = apply_risk_gate(raw, account, market, self._risk_params)
                self._broker.submit(gated)
                steps.append(StepRecord(
                    as_of=day, candidates=candidates, proposed=raw, gated=gated,
                    equity=account.equity, cash=account.cash,
                ))

            # 3. Mark to market at the close.
            self._broker.mark_to_market(day, self._prices_on(frames, symbols, day, "close"))

        return BacktestResult(
            equity_curve=self._broker.equity_curve(),
            steps=tuple(steps),
            fills=self._broker.fills(),
            start=start,
            end=end,
        )

    def _liquidate_delisted(self, frames: Mapping[str, pd.DataFrame], day: date) -> None:
        """Force-exit held names whose price data has ended (delisted) at their last close.

        Without this, a delisted holding is marked at its last price forever and its cash is
        never freed — an optimistic survivorship bias. ``frames[sym].index.max() < day`` means
        the symbol stopped trading before today.
        """
        ts = pd.Timestamp(day)
        for position in self._broker.account_state().positions:
            df = frames.get(position.symbol)
            if df is None or not len(df) or df.index.max() >= ts:
                continue
            last_close = _to_float(df["close"].iloc[-1])
            if last_close is not None and last_close > 0:
                self._broker.liquidate(position.symbol, last_close)

    @staticmethod
    def _prices_on(
        frames: Mapping[str, pd.DataFrame], symbols: Sequence[str], day: date, column: str
    ) -> dict[str, float]:
        ts = pd.Timestamp(day)
        out: dict[str, float] = {}
        for symbol in symbols:
            df = frames.get(symbol)
            if df is None or ts not in df.index:
                continue
            value = _to_float(df.at[ts, column])
            if value is not None and value > 0:
                out[symbol] = value
        return out


def _to_float(value: object) -> float | None:
    """Coerce a frame scalar to a finite float (bar columns are numeric by schema)."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
