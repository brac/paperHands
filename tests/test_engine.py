"""Tests for the backtest engine — end-to-end run, no-look-ahead timing, guard, cadence.

Fully offline: an in-memory FakeProvider supplies deterministic frames; the real
assembler/screen/signals/strategy/risk/SimulatedBroker are wired underneath.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from broker import SimulatedBroker
from core.config import (
    BrokerConfig,
    EngineConfig,
    ScreenConfig,
    SignalConfig,
    StrategyConfig,
)
from core.contracts import AccountState, SymbolMetadata
from data.frame import COLUMNS, INDEX_NAME, empty_bars
from engine import BacktestEngine, BacktestResult
from engine.market import build_market_context
from ingest import LookAheadError
from ingest.assembler import SnapshotAssembler
from ingest.snapshot import MarketSnapshot
from risk import RiskParams
from strategy import StrategyContext

_BUY_EDGE = 1.0006  # slippage 5bps + half of 2bps spread


def _series(base: float = 50.0, slope: float = 0.5, volume: float = 2_000_000.0) -> pd.DataFrame:
    """A long rising daily series (flat intrabar: open==high==low==close==adj_*)."""
    idx = pd.bdate_range("2023-08-01", "2024-01-10")
    vals = [base + slope * i for i in range(len(idx))]
    data = {c: list(vals) for c in COLUMNS}
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
        # Misbehaving: ignore as_of and append a bar dated after the window (look-ahead).
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


def _engine(
    provider, *, rebalance: int = 1, broker: SimulatedBroker | None = None
) -> BacktestEngine:
    return BacktestEngine(
        provider,
        SnapshotAssembler(provider, history_days=600),
        _Universe([SymbolMetadata("RISE", "Rise Co", "Technology")]),
        screen_config=ScreenConfig(),
        signal_config=SignalConfig(),
        strategy_ctx=StrategyContext("rules-only", StrategyConfig(rsi_overbought=200.0)),
        risk_params=RiskParams(),
        broker=broker or SimulatedBroker(BrokerConfig(starting_cash=100_000.0)),
        config=EngineConfig(
            calendar_symbol="RISE", rebalance_every_n_days=rebalance, adv_window=20
        ),
    )


_START, _END = date(2024, 1, 2), date(2024, 1, 9)  # 6 trading days: Jan 2,3,4,5,8,9


def test_end_to_end_run():
    result = _engine(_FakeProvider({"RISE": _series()})).run(_START, _END)
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) == 6
    assert len(result.steps) == 6  # a decision every trading day
    assert result.start == _START and result.end == _END
    assert result.steps[0].as_of == _START
    assert "RISE" in result.steps[0].candidates
    assert result.final_equity() > 0.0


def test_no_look_ahead_fill_at_next_open():
    series = _series()
    result = _engine(_FakeProvider({"RISE": series})).run(_START, _END)
    buys = [f for f in result.fills if f.side == "buy"]
    assert buys, "expected at least one buy fill"
    # The buy decided on Jan 2 fills at Jan 3's open, not Jan 2's.
    open_jan3 = float(series.at[pd.Timestamp("2024-01-03"), "open"])
    open_jan2 = float(series.at[pd.Timestamp("2024-01-02"), "open"])
    assert buys[0].price == pytest.approx(open_jan3 * _BUY_EDGE)
    assert buys[0].price != pytest.approx(open_jan2 * _BUY_EDGE)


def test_look_ahead_guard_raises():
    provider = _FakeProvider({"RISE": _series()}, honor_as_of=False)
    with pytest.raises(LookAheadError):
        _engine(provider).run(_START, _END)


def test_rebalance_cadence():
    result = _engine(_FakeProvider({"RISE": _series()}), rebalance=2).run(_START, _END)
    assert len(result.equity_curve) == 6  # still marks every bar
    assert len(result.steps) == 3  # but decides only on days 0, 2, 4


def test_costs_applied_buy_pays_up():
    series = _series()
    result = _engine(_FakeProvider({"RISE": series})).run(_START, _END)
    buy = next(f for f in result.fills if f.side == "buy")
    assert buy.price > float(series.at[pd.Timestamp("2024-01-03"), "open"])


def test_determinism():
    r1 = _engine(_FakeProvider({"RISE": _series()})).run(_START, _END)
    r2 = _engine(_FakeProvider({"RISE": _series()})).run(_START, _END)
    assert [p.equity for p in r1.equity_curve] == [p.equity for p in r2.equity_curve]


def test_build_market_context():
    snapshot = MarketSnapshot(
        as_of=_START,
        prices={"RISE": _series()},
        account=AccountState(cash=1.0, equity=1.0, buying_power=1.0),
    )
    ctx = build_market_context(snapshot, ["RISE", "MISSING"], adv_window=20)
    assert "MISSING" not in ctx.prices
    assert ctx.prices["RISE"] == pytest.approx(float(_series()["close"].iloc[-1]))
    assert ctx.avg_dollar_volume["RISE"] > 0.0
