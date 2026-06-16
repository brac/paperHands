"""Tests for the SimulatedBroker — next-bar-open fills, cost model, equity, no look-ahead."""

from __future__ import annotations

from datetime import date

import pytest

from broker import Broker, SimulatedBroker
from core.config import BrokerConfig
from core.contracts import ExecutableOrder, ExecutablePlan

# Default cost edge: slippage 5bps + half of 2bps spread = 6bps.
_BUY = 1.0006
_SELL = 0.9994


def _broker(cash: float = 10_000.0, **overrides) -> SimulatedBroker:
    return SimulatedBroker(BrokerConfig(starting_cash=cash, **overrides))


def _plan(*orders: ExecutableOrder) -> ExecutablePlan:
    return ExecutablePlan(orders=orders)


def _buy(symbol: str, qty: float, est: float = 100.0) -> ExecutableOrder:
    return ExecutableOrder(symbol, "buy", qty=qty, est_price=est)


def _sell(symbol: str, qty: float, est: float = 100.0) -> ExecutableOrder:
    return ExecutableOrder(symbol, "sell", qty=qty, est_price=est)


def test_buy_fills_at_next_open_with_costs():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    fills = b.fill_at_open({"AAA": 100.0})
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(100.0 * _BUY)
    assert fills[0].qty == pytest.approx(10.0)
    state = b.account_state()
    assert state.cash == pytest.approx(10_000.0 - 10.0 * 100.0 * _BUY)
    assert state.positions[0].symbol == "AAA"
    assert state.positions[0].avg_price == pytest.approx(100.0 * _BUY)


def test_no_same_bar_leak():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    # Nothing fills until the next bar's open.
    state = b.account_state()
    assert state.positions == ()
    assert state.cash == pytest.approx(10_000.0)


def test_sell_caps_at_held_no_shorting():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    b.fill_at_open({"AAA": 100.0})
    b.submit(_plan(_sell("AAA", 100.0)))  # ask to sell more than held
    fills = b.fill_at_open({"AAA": 110.0})
    assert len(fills) == 1 and fills[0].qty == pytest.approx(10.0)  # capped at held
    assert b.account_state().positions == ()  # fully closed


def test_sell_without_position_no_fill():
    b = _broker(10_000.0)
    b.submit(_plan(_sell("BBB", 5.0)))
    assert b.fill_at_open({"BBB": 50.0}) == []
    assert b.account_state().cash == pytest.approx(10_000.0)


def test_insufficient_cash_scales_buy():
    b = _broker(1_000.0)
    b.submit(_plan(_buy("AAA", 100.0)))  # ~10k of stock on 1k cash
    fills = b.fill_at_open({"AAA": 100.0})
    eff = 100.0 * _BUY
    assert fills[0].qty == pytest.approx(1_000.0 / eff)
    assert b.account_state().cash == pytest.approx(0.0, abs=1e-6)
    assert b.account_state().cash >= 0.0


def test_commission_deducted_and_cost_direction():
    b = _broker(10_000.0, commission_per_order=1.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    fill = b.fill_at_open({"AAA": 100.0})[0]
    assert fill.commission == pytest.approx(1.0)
    assert fill.price > 100.0  # buy pays up
    b.submit(_plan(_sell("AAA", 10.0)))
    sell = b.fill_at_open({"AAA": 100.0})[0]
    assert sell.price < 100.0  # sell receives less


def test_equity_curve_and_mark_to_market():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    b.fill_at_open({"AAA": 100.0})
    b.mark_to_market(date(2024, 5, 21), {"AAA": 105.0})
    curve = b.equity_curve()
    assert len(curve) == 1
    cash = 10_000.0 - 10.0 * 100.0 * _BUY
    assert curve[0].equity == pytest.approx(cash + 10.0 * 105.0)
    assert curve[0].cash == pytest.approx(cash)
    assert curve[0].timestamp == date(2024, 5, 21)


def test_day_pnl_open_to_close():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    b.fill_at_open({"AAA": 100.0})  # day-open equity stamped at open=100
    b.mark_to_market(date(2024, 5, 21), {"AAA": 105.0})
    assert b.account_state().day_pnl == pytest.approx(10.0 * (105.0 - 100.0))


def test_missing_open_price_skips_order():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    assert b.fill_at_open({}) == []  # no quote -> no fill
    assert b.account_state().positions == ()
    assert b.account_state().cash == pytest.approx(10_000.0)


def test_fractional_shares():
    b = _broker(10_000.0)
    b.submit(_plan(_buy("AAA", 2.5)))
    fills = b.fill_at_open({"AAA": 100.0})
    assert fills[0].qty == pytest.approx(2.5)


def test_average_cost_on_add():
    b = _broker(100_000.0)
    b.submit(_plan(_buy("AAA", 10.0)))
    b.fill_at_open({"AAA": 100.0})
    b.submit(_plan(_buy("AAA", 10.0)))
    b.fill_at_open({"AAA": 200.0})
    pos = b.account_state().positions[0]
    assert pos.qty == pytest.approx(20.0)
    assert pos.avg_price == pytest.approx((10 * 100.0 * _BUY + 10 * 200.0 * _BUY) / 20)


def test_implements_broker_protocol():
    assert isinstance(_broker(), Broker)
