"""The screen's output contract — the ranked candidate set plus a dropped-audit trail.

The ``dropped`` trail (symbol + human-readable reason) mirrors
``core.contracts.ExecutablePlan.rejected``: every stage must be inspectable —
"benchmark or it didn't happen".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Candidate:
    """One ranked survivor of the screen."""

    symbol: str
    score: float
    rank: int  # 1-based; 1 is the top-ranked candidate
    sector: str


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """Screen output: the ranked candidates plus an audit of what was dropped and why."""

    candidates: tuple[Candidate, ...] = ()
    dropped: tuple[tuple[str, str], ...] = ()  # (symbol, reason)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(c.symbol for c in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view for the record/audit layer."""
        return {
            "candidates": [asdict(c) for c in self.candidates],
            "dropped": [{"symbol": s, "reason": reason} for s, reason in self.dropped],
        }
