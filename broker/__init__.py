"""Execution interface shared by SimulatedBroker (backtest) and AlpacaBroker (later).

Interface only in this slice. The simulated broker (next-bar fills, cost model) arrives
with the Simulated Broker slice; the Alpaca implementation is Phase 2.
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
