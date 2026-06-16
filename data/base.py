"""The swappable data provider interface (point-in-time correct).

Lives in its own module (not ``__init__``) so implementations and the factory can import it
without a circular dependency on the package's re-exports.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataProvider(Protocol):
    """Swappable source of point-in-time-correct daily bars.

    Implementations must guarantee as-of correctness: a call capped at ``as_of`` must never
    return a bar that postdates ``as_of``. Returned frames follow the schema in
    ``data.frame`` (sorted ``DatetimeIndex``, raw + adjusted OHLCV).
    """

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Return daily bars for ``symbol`` in [start, end], capped at ``as_of`` (def. end)."""
        ...
