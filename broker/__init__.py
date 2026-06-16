"""Execution interface shared by SimulatedBroker (backtest) and AlpacaBroker (later).

The shared ``Broker`` Protocol is the minimal common surface both backends honor —
``submit`` + ``account_state``. The simulation-specific driving methods (fill_at_open,
mark_to_market, equity_curve, fills) live on ``SimulatedBroker`` and are called by the
backtest engine; the live (Alpaca) runner never ticks. Alpaca is Phase 2.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.contracts import AccountState, ExecutablePlan


@runtime_checkable
class Broker(Protocol):
    """Submits gated plans and reports account state. One interface, many backends."""

    def submit(self, plan: ExecutablePlan) -> None:
        """Submit the approved orders of a gated plan for execution."""
        ...

    def account_state(self) -> AccountState:
        """Return current cash, equity, buying power, and open positions."""
        ...

    def open_orders(self) -> tuple[str, ...]:
        """Symbols with a pending (unfilled) open order. Backtest brokers return ``()``."""
        ...


from broker.alpaca import AlpacaBroker, MarketClock, build_alpaca_broker  # noqa: E402
from broker.simulated import EquityPoint, Fill, SimulatedBroker  # noqa: E402

__all__ = [
    "Broker",
    "SimulatedBroker",
    "Fill",
    "EquityPoint",
    "AlpacaBroker",
    "MarketClock",
    "build_alpaca_broker",
]
