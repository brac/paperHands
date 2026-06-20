"""Buy-and-hold SPY benchmark, aligned to the portfolio's marked dates.

The canonical "what if you'd just held SPY?" baseline: invest the same starting capital in
SPY on the first day and mark it daily. Uses ``adj_close`` (dividend-inclusive total return)
and is cost-free — the portfolio pays the §7 cost model, so the comparison is deliberately
conservative (the strategy must beat even a frictionless SPY).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd


def compute_benchmark(
    spy_bars: pd.DataFrame, dates: Sequence[date], starting_cash: float
) -> list[tuple[date, float]]:
    """Return [(date, benchmark_equity)] over ``dates`` from SPY's adjusted close.

    The benchmark is anchored to the first date's adjusted close. Dates missing from the SPY
    frame carry the last known value forward (no look-ahead — only prior data is used).
    """
    if not dates or spy_bars.empty:
        return [(d, starting_cash) for d in dates]

    adj = spy_bars["adj_close"]
    base_ts = pd.Timestamp(dates[0])
    base = float(adj.loc[adj.index <= base_ts].iloc[-1]) if (adj.index <= base_ts).any() \
        else float(adj.iloc[0])

    curve: list[tuple[date, float]] = []
    last = starting_cash
    for d in dates:
        ts = pd.Timestamp(d)
        prior = adj.loc[adj.index <= ts]
        if len(prior) and base > 0:
            last = starting_cash * (float(prior.iloc[-1]) / base)
        curve.append((d, last))
    return curve
