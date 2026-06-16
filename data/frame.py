"""Canonical daily-bar frame schema.

Bars are carried as a pandas ``DataFrame`` (the natural currency for the later signals
"price frames" and the backtrader engine), indexed by a sorted, tz-naive ``DatetimeIndex``
of trading dates, with both raw and split/dividend-**adjusted** OHLCV columns.

Use the adjusted series (``adj_*``) for returns and indicators — raw prices are
discontinuous across splits/dividends and will corrupt any backtest that computes returns
off them. Raw columns are kept for reference/execution-price realism.
"""

from __future__ import annotations

import pandas as pd

# Column order is canonical; every provider maps its payload onto exactly these.
RAW_COLUMNS = ["open", "high", "low", "close", "volume"]
ADJ_COLUMNS = ["adj_open", "adj_high", "adj_low", "adj_close", "adj_volume"]
COLUMNS = RAW_COLUMNS + ADJ_COLUMNS

# Required (non-null) for a usable bar. ``volume`` can legitimately be 0 but not NaN.
REQUIRED_COLUMNS = ["open", "high", "low", "close", "adj_close"]

INDEX_NAME = "date"


def empty_bars() -> pd.DataFrame:
    """An empty, correctly-typed bar frame (the no-data path)."""
    idx = pd.DatetimeIndex([], name=INDEX_NAME)
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS}, index=idx)


def validate_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the schema/invariants of a bar frame and return it unchanged.

    Raises ``ValueError`` on a malformed frame so corruption fails loud at the boundary
    rather than silently propagating into signals.
    """
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bar frame missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("bar frame index must be a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("bar frame index must be sorted ascending")
    if df.index.has_duplicates:
        raise ValueError("bar frame index has duplicate dates")
    if len(df) and df[REQUIRED_COLUMNS].isna().to_numpy().any():
        raise ValueError("bar frame has NaN in required columns")
    return df
