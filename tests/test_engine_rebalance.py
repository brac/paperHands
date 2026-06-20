"""Engine tests for rebalance mode — screen bypass, signal union, target convergence.

Reuses the offline FakeProvider pattern: deterministic flat-price frames for an ETF basket,
the real assembler/signals/rebalancer/gate/SimulatedBroker wired underneath. Confirms the
no-look-ahead guard still fires in rebalance mode and that the same pure brain that runs in
backtest reaches the configured target weights.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from broker import SimulatedBroker
from core.config import (
    BrokerConfig,
    EngineConfig,
    RebalanceConfig,
    ScreenConfig,
    SignalConfig,
    StrategyConfig,
)
from core.contracts import SymbolMetadata
from data.frame import COLUMNS, INDEX_NAME, empty_bars
from engine import BacktestEngine
from ingest import LookAheadError
from ingest.assembler import SnapshotAssembler
from risk import RiskParams
from strategy import StrategyContext


def _flat(price: float = 100.0, volume: float = 5_000_000.0) -> pd.DataFrame:
    """A long flat daily series (constant price), so a rebalanced book stays at target."""
    idx = pd.bdate_range("2023-06-01", "2024-01-10")
    data = {c: [price] * len(idx) for c in COLUMNS}
    data["volume"] = [volume] * len(idx)
    data["adj_volume"] = [volume] * len(idx)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name=INDEX_NAME))


class _FakeProvider:
    def __init__(self, frames: dict[str, pd.DataFrame], *, honor_as_of: bool = True) -> None:
        self._frames = frames
        self._honor = honor_as_of

    def get_daily_bars(self, symbol, start, end, *, as_of=None):
        df = self._frames.get(symbol)
        if df is None:
            return empty_bars()
        eff_end = end if as_of is None else min(end, as_of)
        if self._honor:
            return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(eff_end))]
        sliced = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        future = sliced.iloc[[-1]].copy()
        future.index = pd.DatetimeIndex(
            [pd.Timestamp(end) + pd.Timedelta(days=10)], name=INDEX_NAME)
        return pd.concat([sliced, future])


class _Universe:
    def __init__(self, metas: list[SymbolMetadata]) -> None:
        self._m = {m.symbol: m for m in metas}

    def universe(self):
        return tuple(self._m.values())

    def symbols(self):
        return tuple(self._m.keys())

    def metadata_for(self, symbols):
        return {s: self._m[s] for s in symbols if s in self._m}

    def symbols_in_window(self, start, end):  # noqa: ANN001, ANN202
        return self.symbols()


_UNIVERSE = ("SPY", "BND", "GLD")
_TARGETS = {"SPY": 0.6, "BND": 0.3, "GLD": 0.1}


def _rebalance_engine(provider, *, broker=None, screen_bypass=True) -> BacktestEngine:
    rebalance = RebalanceConfig(target_weights=_TARGETS, trigger="drift", drift_band=0.05)
    return BacktestEngine(
        provider,
        SnapshotAssembler(provider, history_days=600),
        _Universe([SymbolMetadata(s, s, "ETF", "etf") for s in _UNIVERSE]),
        screen_config=ScreenConfig(),
        signal_config=SignalConfig(),
        strategy_ctx=StrategyContext("rebalance", StrategyConfig(), rebalance=rebalance),
        risk_params=RiskParams(sizing="target-weight", max_position_pct=1.0),
        broker=broker or SimulatedBroker(
            BrokerConfig(starting_cash=100_000.0, slippage_bps=0.0, spread_bps=0.0)),
        config=EngineConfig(calendar_symbol="SPY", rebalance_every_n_days=1,
                            adv_window=20, screen_bypass=screen_bypass),
    )


_START, _END = date(2024, 1, 2), date(2024, 1, 10)


def _frames():
    return {s: _flat() for s in _UNIVERSE}


def test_screen_bypass_uses_universe_as_candidates():
    result = _rebalance_engine(_FakeProvider(_frames())).run(_START, _END, universe=_UNIVERSE)
    assert set(result.steps[0].candidates) == set(_UNIVERSE)


def test_rebalance_reaches_target_weights():
    broker = SimulatedBroker(BrokerConfig(starting_cash=100_000.0, slippage_bps=0.0,
                                          spread_bps=0.0))
    engine = _rebalance_engine(_FakeProvider(_frames()), broker=broker)
    engine.run(_START, _END, universe=_UNIVERSE)

    account = broker.account_state()
    equity = account.equity
    weights = {p.symbol: p.qty * 100.0 / equity for p in account.positions}
    # Flat prices + zero costs -> the book converges to the configured targets.
    for symbol, target in _TARGETS.items():
        assert weights.get(symbol, 0.0) == pytest.approx(target, abs=1e-3)


def test_no_look_ahead_guard_still_fires_in_rebalance_mode():
    provider = _FakeProvider(_frames(), honor_as_of=False)
    with pytest.raises(LookAheadError):
        _rebalance_engine(provider).run(_START, _END, universe=_UNIVERSE)


def test_low_turnover_after_convergence():
    # Once at target with flat prices, drift stays within band -> no further trades.
    result = _rebalance_engine(_FakeProvider(_frames())).run(_START, _END, universe=_UNIVERSE)
    # Only the first decision should trade; later steps gate to empty plans.
    traded_steps = [s for s in result.steps if s.gated.orders]
    assert len(traded_steps) == 1
