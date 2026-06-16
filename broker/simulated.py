"""SimulatedBroker — the backtest execution target.

Fills the risk gate's ``ExecutablePlan`` at the **next bar's open** (no same-bar look-ahead)
with a config-driven cost model (slippage + spread; commission >= 0), supports fractional
shares, and tracks the portfolio equity curve + a per-fill audit trail for §9.

Driven bar-by-bar by the engine (§8): per bar T — ``fill_at_open(open_prices_T)`` executes
orders queued during T-1; the pipeline then runs and ``submit``s a plan for T+1; finally
``mark_to_market(date_T, close_prices_T)`` records the equity point. A plan therefore never
fills on the bar it was decided.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.config import BrokerConfig
from core.contracts import (
    AccountState,
    ExecutableOrder,
    ExecutablePlan,
    Position,
    Side,
    _is_finite_number,
)

_QTY_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class Fill:
    """A single executed trade (post-cost), for the audit trail."""

    symbol: str
    side: Side
    qty: float
    price: float  # effective fill price, incl. slippage + half-spread
    commission: float


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One point on the portfolio equity curve."""

    timestamp: date
    equity: float
    cash: float


@dataclass(slots=True)
class _Lot:
    """Internal mutable holding: quantity + weighted-average cost."""

    qty: float
    avg_price: float


class SimulatedBroker:
    """A long-only cash-account broker simulation. Implements the shared ``Broker`` Protocol."""

    def __init__(self, config: BrokerConfig) -> None:
        self._config = config
        self._cash = config.starting_cash
        self._positions: dict[str, _Lot] = {}
        self._pending: tuple[ExecutableOrder, ...] = ()
        self._fills: list[Fill] = []
        self._equity_curve: list[EquityPoint] = []
        self._last_prices: dict[str, float] = {}
        self._day_open_equity: float | None = None

    # -- Broker Protocol --------------------------------------------------------------
    def submit(self, plan: ExecutablePlan) -> None:
        """Queue a gated plan's orders for execution at the next open (one plan per bar)."""
        self._pending = plan.orders

    def account_state(self) -> AccountState:
        """Cash, mark-to-market equity, buying power (= cash), positions, and session P&L."""
        equity = self._equity()
        day_pnl = 0.0 if self._day_open_equity is None else equity - self._day_open_equity
        positions = tuple(
            Position(symbol, lot.qty, lot.avg_price)
            for symbol, lot in sorted(self._positions.items())
        )
        return AccountState(
            cash=self._cash,
            equity=equity,
            buying_power=self._cash,
            positions=positions,
            day_pnl=day_pnl,
        )

    def open_orders(self) -> tuple[str, ...]:
        """No async open orders in simulation — pending plans fill on the next bar's open."""
        return ()

    # -- Simulation driving (called by the backtest engine) ---------------------------
    def fill_at_open(self, open_prices: Mapping[str, float]) -> list[Fill]:
        """Execute the queued orders at this bar's open prices (with costs). Returns fills."""
        fills: list[Fill] = []
        for order in self._pending:
            price = open_prices.get(order.symbol)
            if price is None or not _is_finite_number(price) or price <= 0:
                continue  # no usable quote -> cannot fill
            fill = self._execute(order, float(price))
            if fill is not None:
                fills.append(fill)
        self._pending = ()
        self._fills.extend(fills)
        # Stamp day-open equity at the open so account_state().day_pnl is meaningful.
        self._update_prices(open_prices)
        self._day_open_equity = self._equity()
        return fills

    def mark_to_market(self, timestamp: date, prices: Mapping[str, float]) -> None:
        """Update last-known prices and append an equity-curve point."""
        self._update_prices(prices)
        self._equity_curve.append(EquityPoint(timestamp, self._equity(), self._cash))

    def equity_curve(self) -> tuple[EquityPoint, ...]:
        return tuple(self._equity_curve)

    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    # -- internals --------------------------------------------------------------------
    def _execute(self, order: ExecutableOrder, open_price: float) -> Fill | None:
        eff = self._effective_price(open_price, order.side)
        commission = self._config.commission_per_order

        if order.side == "buy":
            cash_for_shares = self._cash - commission
            if cash_for_shares <= 0.0:
                return None
            qty = order.qty
            if qty * eff > cash_for_shares:  # scale to the affordable fractional qty
                qty = cash_for_shares / eff
            if not _is_finite_number(qty) or qty <= 0.0:
                return None
            self._cash -= qty * eff + commission
            self._add_to_lot(order.symbol, qty, eff)
            return Fill(order.symbol, "buy", qty, eff, commission)

        # sell — long-only: never sell more than held (no shorting).
        lot = self._positions.get(order.symbol)
        held = lot.qty if lot is not None else 0.0
        qty = min(order.qty, held) if order.qty > 0.0 else held
        if qty <= 0.0:
            return None
        self._cash += qty * eff - commission
        self._reduce_lot(order.symbol, qty)
        return Fill(order.symbol, "sell", qty, eff, commission)

    def _effective_price(self, open_price: float, side: Side) -> float:
        edge = (self._config.slippage_bps + self._config.spread_bps / 2.0) / 10_000.0
        return open_price * (1.0 + edge) if side == "buy" else open_price * (1.0 - edge)

    def _add_to_lot(self, symbol: str, qty: float, price: float) -> None:
        lot = self._positions.get(symbol)
        if lot is None:
            self._positions[symbol] = _Lot(qty=qty, avg_price=price)
            return
        total = lot.qty + qty
        lot.avg_price = (lot.qty * lot.avg_price + qty * price) / total if total > 0 else price
        lot.qty = total

    def _reduce_lot(self, symbol: str, qty: float) -> None:
        lot = self._positions[symbol]
        lot.qty -= qty
        if lot.qty <= _QTY_EPSILON:
            del self._positions[symbol]

    def _update_prices(self, prices: Mapping[str, float]) -> None:
        for symbol, price in prices.items():
            if _is_finite_number(price) and price > 0:
                self._last_prices[symbol] = float(price)

    def _equity(self) -> float:
        """Cash + Σ qty·price, using last-known prices (avg cost as a fallback)."""
        total = self._cash
        for symbol, lot in self._positions.items():
            total += lot.qty * self._last_prices.get(symbol, lot.avg_price)
        return total
