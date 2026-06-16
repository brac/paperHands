"""Tests for AlpacaBroker — all offline (the TradingClient is injected/stubbed).

No network and no installed ``alpaca-py``: the broker depends on a structural Protocol, the
SDK import is lazy (inside ``build_alpaca_broker`` / ``_build_order``), and the live guard is
factored into ``_assert_live_allowed`` so it can be checked without constructing a real client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from broker import AlpacaBroker, MarketClock
from broker.alpaca import LIVE_CONFIRM_TOKEN, _assert_live_allowed
from core.config import ExecConfig, Settings
from core.contracts import ExecutableOrder, ExecutablePlan


# -- Fakes (Alpaca-shaped, numeric fields as strings like the real API) ------------------
@dataclass
class _FakeAccount:
    cash: str = "25000.50"
    equity: str = "100000.00"
    buying_power: str = "50000.00"
    last_equity: str = "99000.00"


@dataclass
class _FakePosition:
    symbol: str
    qty: str
    avg_entry_price: str


@dataclass
class _FakeSubmitted:
    id: str


@dataclass
class _FakeOrder:
    symbol: str


@dataclass
class _FakeClock:
    is_open: bool
    next_open: datetime
    next_close: datetime


class _FakeClient:
    """Records submitted order requests and returns canned account/position objects."""

    def __init__(
        self,
        account: _FakeAccount | None = None,
        positions: list[_FakePosition] | None = None,
        orders: list[_FakeOrder] | None = None,
        clock: _FakeClock | None = None,
    ) -> None:
        self._account = account if account is not None else _FakeAccount()
        self._positions = positions if positions is not None else []
        self._orders = orders if orders is not None else []
        self._clock = clock
        self.submitted: list[Any] = []
        self.orders_query: Any = "<unset>"
        self._next_id = 0

    def get_account(self) -> _FakeAccount:
        return self._account

    def get_all_positions(self) -> list[_FakePosition]:
        return list(self._positions)

    def submit_order(self, order_data: Any) -> _FakeSubmitted:
        self.submitted.append(order_data)
        self._next_id += 1
        return _FakeSubmitted(id=f"order-{self._next_id}")

    def get_orders(self, filter: Any = None) -> list[_FakeOrder]:  # noqa: A002 - alpaca name
        self.orders_query = filter
        return list(self._orders)

    def get_clock(self) -> _FakeClock:
        assert self._clock is not None
        return self._clock


def _stub_order_factory(
    symbol: str, side: str, qty: float, time_in_force: str, fractional: bool
) -> dict[str, Any]:
    """A plain-dict order request — no alpaca-py needed to exercise submit()."""
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "time_in_force": time_in_force,
        "fractional": fractional,
    }


def _broker(client: _FakeClient, *, live_trading: bool = False, **exec_kw: Any) -> AlpacaBroker:
    return AlpacaBroker(
        client,
        execution=ExecConfig(**exec_kw),
        live_trading=live_trading,
        live_confirm=None,
        order_factory=_stub_order_factory,
        # Sentinel query — keeps the real alpaca-py GetOrdersRequest import out of tests.
        open_orders_query=lambda: "OPEN",
    )


# -- account_state mapping ---------------------------------------------------------------
def test_account_state_maps_and_coerces_strings():
    client = _FakeClient(
        positions=[
            _FakePosition(symbol="AAA", qty="10.5", avg_entry_price="100.25"),
            _FakePosition(symbol="BBB", qty="3", avg_entry_price="50.0"),
        ]
    )
    state = _broker(client).account_state()

    assert state.cash == pytest.approx(25000.50)
    assert state.equity == pytest.approx(100000.00)
    assert state.buying_power == pytest.approx(50000.00)
    # day_pnl = equity - last_equity = 100000 - 99000.
    assert state.day_pnl == pytest.approx(1000.0)
    assert state.positions[0].symbol == "AAA"
    assert state.positions[0].qty == pytest.approx(10.5)
    assert state.positions[0].avg_price == pytest.approx(100.25)
    assert state.positions[1].qty == pytest.approx(3.0)


def test_account_state_day_pnl_zero_without_last_equity():
    account = _FakeAccount(last_equity=None)
    state = _broker(_FakeClient(account=account)).account_state()
    assert state.day_pnl == 0.0


def test_account_state_coerces_garbage_to_zero():
    account = _FakeAccount(cash="not-a-number")
    state = _broker(_FakeClient(account=account)).account_state()
    assert state.cash == 0.0


# -- submit ------------------------------------------------------------------------------
def test_submit_maps_orders_and_records_ids():
    client = _FakeClient()
    broker = _broker(client)
    plan = ExecutablePlan(
        orders=(
            ExecutableOrder("AAA", "buy", qty=2.5, est_price=100.0),
            ExecutableOrder("BBB", "sell", qty=1.0, est_price=50.0),
        )
    )
    broker.submit(plan)

    assert len(client.submitted) == 2
    assert broker.last_orders == ("order-1", "order-2")
    # ExecutableOrder fields map onto the (fractional, day) request per ExecConfig defaults.
    assert client.submitted[0] == {
        "symbol": "AAA",
        "side": "buy",
        "qty": 2.5,
        "time_in_force": "day",
        "fractional": True,
    }
    assert client.submitted[1]["side"] == "sell"


def test_empty_plan_submits_nothing():
    client = _FakeClient()
    broker = _broker(client)
    broker.submit(ExecutablePlan())
    assert client.submitted == []
    assert broker.last_orders == ()


# -- open orders -------------------------------------------------------------------------
def test_open_orders_maps_symbols_deduped_and_sorted():
    client = _FakeClient(
        orders=[_FakeOrder("MSFT"), _FakeOrder("AAPL"), _FakeOrder("MSFT")]
    )
    broker = _broker(client)
    assert broker.open_orders() == ("AAPL", "MSFT")
    # The injected query is the object handed to the client's get_orders(filter=...).
    assert client.orders_query == "OPEN"


def test_open_orders_empty_when_none_pending():
    assert _broker(_FakeClient(orders=[])).open_orders() == ()


# -- market clock ------------------------------------------------------------------------
def test_market_clock_maps_fields():
    open_dt = datetime(2026, 6, 17, 9, 30)
    close_dt = datetime(2026, 6, 17, 16, 0)
    client = _FakeClient(clock=_FakeClock(is_open=True, next_open=open_dt, next_close=close_dt))
    clock = _broker(client).market_clock()
    assert clock == MarketClock(is_open=True, next_open=open_dt, next_close=close_dt)


# -- endpoint selection ------------------------------------------------------------------
def test_paper_endpoint_by_default():
    broker = _broker(_FakeClient(), live_trading=False)
    assert broker.is_live is False
    assert broker.base_url == ExecConfig().paper_base_url


def test_live_endpoint_only_when_live_trading():
    broker = _broker(_FakeClient(), live_trading=True)
    assert broker.is_live is True
    assert broker.base_url == ExecConfig().live_base_url


# -- live guard (_assert_live_allowed / build_alpaca_broker composition root) -------------
def _settings(*, live_trading: bool, live_confirm: str | None) -> Settings:
    """Build Settings deterministically (ignore any developer .env) via the field aliases."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        LIVE_TRADING=live_trading,
        LIVE_CONFIRM=live_confirm,
    )


def test_guard_allows_paper():
    _assert_live_allowed(_settings(live_trading=False, live_confirm=None))  # no raise


def test_guard_refuses_live_without_confirm():
    with pytest.raises(RuntimeError, match="Refusing to construct a LIVE"):
        _assert_live_allowed(_settings(live_trading=True, live_confirm=None))


def test_guard_refuses_live_with_wrong_confirm():
    with pytest.raises(RuntimeError):
        _assert_live_allowed(_settings(live_trading=True, live_confirm="yes"))


def test_guard_allows_live_with_correct_confirm():
    _assert_live_allowed(_settings(live_trading=True, live_confirm=LIVE_CONFIRM_TOKEN))


def test_build_alpaca_broker_refuses_live_without_confirm():
    # The guard runs before the lazy SDK import, so this raises with no alpaca-py installed.
    from broker import build_alpaca_broker

    with pytest.raises(RuntimeError, match="Refusing to construct a LIVE"):
        build_alpaca_broker(_settings(live_trading=True, live_confirm=None))
