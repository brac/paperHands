"""Engine test for yolo mode — screen bypass, hype ranking, concentration, no look-ahead.

Reuses the offline FakeProvider pattern: deterministic trended price/volume frames so the
hottest names are unambiguous, with the real assembler/signals/yolo/gate/SimulatedBroker wired
underneath. Confirms the same pure brain that runs in backtest piles into the top-N movers and
that the no-look-ahead guard still fires in yolo mode.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from broker import SimulatedBroker
from core.config import (
    BrokerConfig,
    EngineConfig,
    ScreenConfig,
    SignalConfig,
    StrategyConfig,
    YoloConfig,
)
from core.contracts import SymbolMetadata
from data.frame import COLUMNS, INDEX_NAME, empty_bars
from engine import BacktestEngine
from ingest import LookAheadError
from ingest.assembler import SnapshotAssembler
from risk import RiskParams
from strategy import StrategyContext

_IDX = pd.bdate_range("2023-01-02", "2024-01-12")


def _trend(start: float, end: float, volume: float = 5_000_000.0) -> pd.DataFrame:
    """A linear price path from ``start`` to ``end`` with constant volume."""
    path = np.linspace(start, end, len(_IDX))
    data = {c: path.copy() for c in COLUMNS}
    data["volume"] = [volume] * len(_IDX)
    data["adj_volume"] = [volume] * len(_IDX)
    return pd.DataFrame(data, index=pd.DatetimeIndex(_IDX, name=INDEX_NAME))


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

    def symbols(self):
        return tuple(self._m.keys())

    def metadata_for(self, symbols):
        return {s: self._m[s] for s in symbols if s in self._m}

    def symbols_in_window(self, start, end):  # noqa: ANN001, ANN202
        return self.symbols()


# HOT and WARM trend up (positive roc); COLD trends down (zero hype). SPY is the calendar.
_FRAMES = {
    "HOT": _trend(50.0, 150.0),
    "WARM": _trend(90.0, 120.0),
    "COLD": _trend(150.0, 60.0),
    "SPY": _trend(100.0, 110.0),
}
_UNIVERSE = ("HOT", "WARM", "COLD")
_START, _END = date(2023, 12, 1), date(2024, 1, 12)


def _yolo_engine(provider, *, broker=None) -> BacktestEngine:
    yolo = YoloConfig(top_n=2, max_position_pct=0.6, conviction_weighted=True)
    return BacktestEngine(
        provider,
        SnapshotAssembler(provider, history_days=600),
        _Universe([SymbolMetadata(s, s, "Tech", "equity") for s in _UNIVERSE]),
        screen_config=ScreenConfig(),
        signal_config=SignalConfig(),
        strategy_ctx=StrategyContext("yolo", StrategyConfig(), yolo=yolo),
        risk_params=RiskParams(sizing="target-weight", max_position_pct=0.6, max_positions=5),
        broker=broker or SimulatedBroker(
            BrokerConfig(starting_cash=100_000.0, slippage_bps=0.0, spread_bps=0.0)),
        config=EngineConfig(calendar_symbol="SPY", rebalance_every_n_days=5,
                            adv_window=20, screen_bypass=True),
    )


def test_yolo_concentrates_into_the_hottest_names():
    broker = SimulatedBroker(
        BrokerConfig(starting_cash=100_000.0, slippage_bps=0.0, spread_bps=0.0))
    _yolo_engine(_FakeProvider(_FRAMES), broker=broker).run(_START, _END, universe=_UNIVERSE)

    held = {p.symbol for p in broker.account_state().positions if p.qty > 0}
    assert "HOT" in held  # the strongest momentum name is always bought
    assert "COLD" not in held  # the downtrending name never scores positive hype
    # Concentrated: at most top_n names held, and the book is meaningfully deployed.
    assert len(held) <= 2
    assert broker.account_state().cash < 100_000.0


def test_yolo_respects_top_n_cap_on_position_count():
    result = _yolo_engine(_FakeProvider(_FRAMES)).run(_START, _END, universe=_UNIVERSE)
    traded = [s for s in result.steps if s.gated.orders]
    assert traded  # it did trade
    for step in result.steps:
        buys = [o for o in step.gated.orders if o.side == "buy"]
        assert len(buys) <= 2  # never more than top_n new names in a single decision


def test_no_look_ahead_guard_still_fires_in_yolo_mode():
    provider = _FakeProvider(_FRAMES, honor_as_of=False)
    with pytest.raises(LookAheadError):
        _yolo_engine(provider).run(_START, _END, universe=_UNIVERSE)
