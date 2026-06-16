"""Assembles an immutable point-in-time MarketSnapshot from the active feeds.

Pulls as-of-capped price history from the injected ``DataProvider``, attaches the secondary
feed outputs, runs the no-look-ahead guard, and returns a ``MarketSnapshot``. Does I/O via
its injected dependencies only — it never imports a concrete provider or the broker (the
account is passed in, since the broker that supplies it is a later slice).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from core.contracts import AccountState
from data.base import DataProvider
from ingest.feeds import (
    FilingsProvider,
    MacroProvider,
    NewsProvider,
    NullFilings,
    NullMacro,
    NullNews,
)
from ingest.guard import assert_no_look_ahead
from ingest.snapshot import MarketSnapshot


class SnapshotAssembler:
    """Builds a ``MarketSnapshot`` for a universe at an as-of date."""

    def __init__(
        self,
        data_provider: DataProvider,
        *,
        filings: FilingsProvider | None = None,
        news: NewsProvider | None = None,
        macro: MacroProvider | None = None,
        history_days: int = 600,
    ) -> None:
        self._data = data_provider
        self._filings: FilingsProvider = filings or NullFilings()
        self._news: NewsProvider = news or NullNews()
        self._macro: MacroProvider = macro or NullMacro()
        self._history_days = history_days

    def assemble(
        self, symbols: Sequence[str], as_of: date, account: AccountState
    ) -> MarketSnapshot:
        """Assemble the point-in-time snapshot. Raises ``LookAheadError`` on any future datum."""
        start = as_of - timedelta(days=self._history_days)
        prices = {
            symbol: self._data.get_daily_bars(symbol, start, as_of, as_of=as_of)
            for symbol in symbols
        }
        assert_no_look_ahead(prices, as_of)

        return MarketSnapshot(
            as_of=as_of,
            prices=prices,
            account=account,
            filings=dict(self._filings.flags_as_of(symbols, as_of)),
            news=dict(self._news.context_as_of(symbols, as_of)),
            macro=dict(self._macro.values_as_of(as_of)),
        )
