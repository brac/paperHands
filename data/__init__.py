"""Historical/live data provider interface (point-in-time correct).

Interface only in this slice. The default Tiingo implementation (with local caching) and
optional Polygon impl arrive with the Data Provider slice.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataProvider(Protocol):
    """Swappable source of point-in-time-correct daily bars.

    Implementations must guarantee as-of correctness: a call asking for data as of date D
    must never return information that postdates D.
    """

    def get_daily_bars(self, symbol: str, start: date, end: date) -> Any:
        """Return daily OHLCV bars for ``symbol`` within [start, end]. Shape TBD by impl."""
        ...
