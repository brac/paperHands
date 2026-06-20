"""Build the risk gate's MarketContext from a point-in-time snapshot.

The gate needs a narrow view — latest tradeable price + average dollar volume per symbol —
distinct from the rich `MarketSnapshot`. Prices use the raw `close` as the decision-time
estimate (the actual fill is the next bar's open, unknown now). Symbols absent from the
snapshot are omitted; the gate treats them as untradeable.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.contracts import MarketContext, _is_finite_number
from ingest.snapshot import MarketSnapshot


def build_market_context(
    snapshot: MarketSnapshot, symbols: Sequence[str], adv_window: int
) -> MarketContext:
    prices: dict[str, float] = {}
    avg_dollar_volume: dict[str, float] = {}
    for symbol in symbols:
        df = snapshot.prices.get(symbol)
        if df is None or len(df) == 0:
            continue
        last_close = float(df["close"].iloc[-1])
        if not _is_finite_number(last_close) or last_close <= 0:
            continue
        prices[symbol] = last_close
        window = df.iloc[-adv_window:]
        adv = float((window["close"] * window["volume"]).mean())
        avg_dollar_volume[symbol] = adv if _is_finite_number(adv) else 0.0
    return MarketContext(prices=prices, avg_dollar_volume=avg_dollar_volume)
