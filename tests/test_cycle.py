"""Tests for the one-shot live paper cycle — dry-run safety, submit on a normal run, abort.

The whole cycle is driven with injected stubs: a stub broker (canned ``account_state``,
recording ``submit``) and a stub data provider (synthetic bars), plus a temp ``CycleStore``.
No network and no Alpaca SDK — the cycle reuses the Phase-1 pure pipeline verbatim.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from core.config import Settings
from core.contracts import AccountState, ExecutablePlan, Position
from data.frame import COLUMNS, INDEX_NAME
from record.cycle_store import CycleStore
from runner.cycle import run_cycle

_AS_OF = date(2024, 5, 20)
_UNIVERSE = ["AAPL"]  # a real seed symbol so the screen has sector metadata


def _synthetic_bars(
    latest_close: float, daily_volume: float, *, trending: bool
) -> pd.DataFrame:
    """A 100-bar frame: flat raw close (liquidity/min-price) + an adj_close path for the ROC.

    ``trending=True`` rises ~20% with a sawtooth oscillation, so ROC > 0 and trend is up while
    RSI stays below overbought (a pure monotonic ramp pegs RSI at 100 and suppresses the buy).
    ``trending=False`` is flat -> no momentum, no buy.
    """
    n = 100
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n), name=INDEX_NAME)
    if trending:
        start_adj = latest_close * 0.80
        adj = [
            start_adj + (latest_close - start_adj) * (i / (n - 1)) + 3.0 * math.sin(i / 1.5)
            for i in range(n)
        ]
    else:
        adj = [latest_close] * n
    data = {c: [latest_close] * n for c in COLUMNS}
    data["volume"] = [daily_volume] * n
    data["adj_close"] = adj
    return pd.DataFrame(data, index=idx)


class _StubProvider:
    """A ``DataProvider`` that returns the same strong-momentum frame for every symbol."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def get_daily_bars(self, symbol, start, end, *, as_of=None):  # noqa: ANN001, ANN204
        return self._frame


class _StubBroker:
    """A ``Broker`` with a canned account and a recording ``submit`` (no network)."""

    def __init__(self, account: AccountState) -> None:
        self._account = account
        self.submitted: list[ExecutablePlan] = []

    def account_state(self) -> AccountState:
        return self._account

    def submit(self, plan: ExecutablePlan) -> None:
        self.submitted.append(plan)


class _ExplodingBroker(_StubBroker):
    """A broker whose ``account_state`` raises — to prove a stage failure aborts before submit."""

    def account_state(self) -> AccountState:
        raise RuntimeError("alpaca account fetch failed")


def _settings() -> Settings:
    # Defaults are rules-only + sane risk caps; no env/secrets needed for the stubbed cycle.
    return Settings()


def _account() -> AccountState:
    return AccountState(cash=100_000.0, equity=100_000.0, buying_power=100_000.0)


def _run(broker, provider, store, **kwargs):  # noqa: ANN001, ANN003
    return run_cycle(
        _settings(),
        as_of=_AS_OF,
        universe=_UNIVERSE,
        broker=broker,
        provider=provider,
        store=store,
        **kwargs,
    )


def test_dry_run_never_submits(tmp_path):
    broker = _StubBroker(_account())
    provider = _StubProvider(_synthetic_bars(150.0, 5_000_000, trending=True))
    store = CycleStore(tmp_path / "cycles.sqlite")

    cycle_id = _run(broker, provider, store, dry_run=True)

    assert broker.submitted == []  # dry_run must never round-trip to the broker
    assert store.load_cycle(cycle_id).as_of == _AS_OF  # but the cycle is still recorded


def test_normal_run_submits_the_gated_plan(tmp_path):
    broker = _StubBroker(_account())
    provider = _StubProvider(_synthetic_bars(150.0, 5_000_000, trending=True))
    store = CycleStore(tmp_path / "cycles.sqlite")

    cycle_id = _run(broker, provider, store, dry_run=False)

    assert len(broker.submitted) == 1  # a normal run submits exactly the gated plan
    recorded = store.load_cycle(cycle_id)
    assert broker.submitted[0].orders == recorded.gated.orders
    assert len(recorded.gated.orders) >= 1  # strong momentum + cash -> at least one buy


def test_no_orders_means_no_submit(tmp_path):
    # A held position with no buy candidates and a flat (non-bearish) market -> empty plan.
    broker = _StubBroker(
        AccountState(
            cash=100_000.0, equity=100_000.0, buying_power=100_000.0,
            positions=(Position("ZZZZ", qty=1.0, avg_price=10.0),),
        )
    )
    provider = _StubProvider(_synthetic_bars(150.0, 5_000_000, trending=False))
    store = CycleStore(tmp_path / "cycles.sqlite")

    _run(broker, provider, store, dry_run=False)

    assert broker.submitted == []  # nothing approved -> never submit


def test_stage_failure_aborts_before_submit(tmp_path):
    broker = _ExplodingBroker(_account())
    provider = _StubProvider(_synthetic_bars(150.0, 5_000_000, trending=True))
    store = CycleStore(tmp_path / "cycles.sqlite")

    with pytest.raises(RuntimeError, match="alpaca account fetch failed"):
        _run(broker, provider, store, dry_run=False)

    assert broker.submitted == []  # aborted before any order reached the broker
    assert store.list_cycles() == []  # and nothing was recorded
