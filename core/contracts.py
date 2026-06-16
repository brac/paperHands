"""Data contracts shared across the pipeline.

Frozen, type-hinted, JSON-serializable dataclasses. These are the boundaries between the
pure stages (screen -> signals -> strategy -> risk -> execute). Keeping them immutable
makes look-ahead and accidental mutation structurally impossible to introduce downstream.

Only the contracts needed by the current slice (account state, market context, proposed
and executable plans) live here. SignalSet / MarketSnapshot arrive with their slices.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Action = Literal["buy", "sell", "hold"]
Side = Literal["buy", "sell"]


def _is_finite_number(value: object) -> bool:
    """True only for a real, finite int/float (rejects bool, NaN, inf, non-numbers)."""
    # bool is a subclass of int; a boolean is never a valid quantity or weight here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


@dataclass(frozen=True, slots=True)
class Position:
    """An open position in the account."""

    symbol: str
    qty: float
    avg_price: float


@dataclass(frozen=True, slots=True)
class AccountState:
    """Point-in-time account snapshot the risk gate sizes against."""

    cash: float
    equity: float
    buying_power: float
    positions: tuple[Position, ...] = ()
    day_pnl: float = 0.0  # signed P&L for the current session, in account currency

    def position_symbols(self) -> frozenset[str]:
        return frozenset(p.symbol for p in self.positions)


@dataclass(frozen=True, slots=True)
class MarketContext:
    """At-decision market facts the gate needs: latest price + liquidity per symbol.

    Sourced point-in-time from the active feed. The gate treats a symbol absent from
    ``prices`` as unknown/untradeable.
    """

    prices: dict[str, float] = field(default_factory=dict)
    avg_dollar_volume: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    """One line of a strategy's proposed plan (the LLM/rules output schema).

    ``target_weight`` is the desired fraction of equity for the symbol (buys). It is
    deliberately unvalidated here — validation/clamping is the risk gate's sovereign job.
    """

    action: Action
    symbol: str
    target_weight: float = 0.0
    conviction: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProposedPlan:
    """A strategy's full proposal, pre-gate."""

    orders: tuple[ProposedOrder, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutableOrder:
    """A safe, sized order the gate has approved for execution."""

    symbol: str
    side: Side
    qty: float  # fractional shares; always finite and > 0
    est_price: float


@dataclass(frozen=True, slots=True)
class ExecutablePlan:
    """Risk-gate output: approved orders plus an audit trail of what was rejected.

    The ``rejected`` list (proposed order + human-readable reason) exists because the
    project requires every cycle to be inspectable — "benchmark or it didn't happen".
    """

    orders: tuple[ExecutableOrder, ...] = ()
    rejected: tuple[tuple[ProposedOrder, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view for the record/audit layer."""
        return {
            "orders": [asdict(o) for o in self.orders],
            "rejected": [{"order": asdict(o), "reason": reason} for o, reason in self.rejected],
        }
