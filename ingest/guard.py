"""The no-look-ahead guard — the cardinal backtesting safety check.

Pure function reused by the assembler (every cycle) and by the guard test (a deliberate
look-ahead attempt). The price provider already caps at ``as_of``; this is the boundary that
fails loud if any provider/feed ever leaks a future datum. Defense in depth.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pandas as pd


class LookAheadError(Exception):
    """Raised when a snapshot would contain data dated after its ``as_of`` timestamp."""


def assert_no_look_ahead(prices: Mapping[str, pd.DataFrame], as_of: date) -> None:
    """Raise ``LookAheadError`` if any price frame holds a bar dated after ``as_of``."""
    cutoff = pd.Timestamp(as_of)
    for symbol, df in prices.items():
        if len(df) and df.index.max() > cutoff:
            raise LookAheadError(
                f"{symbol}: bar dated {df.index.max().date()} postdates as_of {as_of}"
            )
