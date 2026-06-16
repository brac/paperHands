"""Compute a SignalSet per candidate from a point-in-time snapshot.

Pure: no I/O, no clock, deterministic. Reads ``snapshot.prices`` / ``.filings`` / ``.news`` by
duck typing, so ``MarketSnapshot`` is imported under ``TYPE_CHECKING`` only — keeping
``signals`` free of any runtime dependency on the ``ingest -> data -> core.config`` chain
(the same decoupling ``screen/screen.py`` uses).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from core.config import SignalConfig
from core.contracts import FilingFlags
from signals.indicators import atr, roc, rsi, sma, zscore
from signals.signalset import SignalSet

if TYPE_CHECKING:
    from ingest.snapshot import MarketSnapshot

_NO_FILINGS = FilingFlags()


def compute_signals(
    snapshot: MarketSnapshot,
    candidates: Sequence[str],
    config: SignalConfig,
) -> dict[str, SignalSet]:
    """Build a ``SignalSet`` for each candidate that has price data in the snapshot.

    Candidates absent from ``snapshot.prices`` are skipped. Returned dict preserves the
    candidate order.
    """
    out: dict[str, SignalSet] = {}
    for symbol in candidates:
        df = snapshot.prices.get(symbol)
        if df is None:
            continue

        latest_close = float(df["close"].iloc[-1]) if len(df) else None

        sma_short = sma(df, config.sma_short_window)
        sma_long = sma(df, config.sma_long_window)
        trend_strength = None
        if sma_short is not None and sma_long is not None and sma_long != 0.0:
            trend_strength = sma_short / sma_long - 1.0

        atr_abs = atr(df, config.atr_window)
        atr_pct = None
        if atr_abs is not None and latest_close is not None and latest_close != 0.0:
            atr_pct = atr_abs / latest_close

        filings = snapshot.filings.get(symbol) or _NO_FILINGS
        news = snapshot.news.get(symbol)

        out[symbol] = SignalSet(
            symbol=symbol,
            price=latest_close,
            sma_short=sma_short,
            sma_long=sma_long,
            trend_strength=trend_strength,
            roc=roc(df, config.roc_window),
            rsi=rsi(df, config.rsi_window),
            atr_pct=atr_pct,
            zscore=zscore(df, config.zscore_window),
            recent_8k=filings.recent_8k,
            recent_insider_buy=filings.recent_insider_buy,
            news_sentiment=news.sentiment if news is not None else None,
        )
    return out
