"""The immutable point-in-time MarketSnapshot — the ingest layer's output.

Every downstream pure stage (screen -> signals -> strategy) reasons over a snapshot and
nothing else. It carries pandas price frames, so it lives here (in ``ingest/``) rather than
in ``core/contracts.py``, which is reserved for JSON-serializable contracts.

Not to be confused with ``core.contracts.MarketContext`` — that is the risk gate's narrow
input (latest price + ADV), derived from a snapshot in a later slice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.contracts import AccountState, FilingFlags, HypeContext, NewsContext


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Everything known at ``as_of`` and nothing that postdates it.

    Invariant (enforced by ``ingest.guard``): no datum in this object is dated after
    ``as_of``.
    """

    as_of: date
    prices: Mapping[str, pd.DataFrame]
    account: AccountState
    filings: Mapping[str, FilingFlags] = field(default_factory=dict)
    news: Mapping[str, NewsContext] = field(default_factory=dict)
    macro: Mapping[str, float] = field(default_factory=dict)
    social: Mapping[str, HypeContext] = field(default_factory=dict)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.prices.keys())

    def summary(self) -> str:
        """One-line human summary for logging."""
        bars = " ".join(f"{s}={len(df)}" for s, df in self.prices.items())
        return f"as_of={self.as_of} symbols={len(self.prices)} bars[{bars}]"
