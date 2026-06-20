"""Secondary-feed interfaces (news / filings / macro) + null implementations.

These are the *secondary* signals of the system's doctrine — technicals are primary; news
and filings may only adjust conviction or veto, never originate a trade. In Phase 1 the
integration is shallow: the interfaces exist and feed the snapshot, with null defaults that
return nothing. Real SEC EDGAR / FRED clients arrive in a later (parallelizable) slice.

**Point-in-time contract:** every implementation must return only information available
at-or-before ``as_of`` — same no-look-ahead rule the price provider obeys.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from core.contracts import FilingFlags, HypeContext, NewsContext


@runtime_checkable
class FilingsProvider(Protocol):
    """Per-symbol SEC-filing flags as of a date (recent 8-K / Form 4)."""

    def flags_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, FilingFlags]:
        ...


@runtime_checkable
class NewsProvider(Protocol):
    """Per-symbol news context as of a date."""

    def context_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, NewsContext]:
        ...


@runtime_checkable
class MacroProvider(Protocol):
    """Macro series (e.g. FRED) latest values as of a date, keyed by series id."""

    def values_as_of(self, as_of: date) -> Mapping[str, float]:
        ...


@runtime_checkable
class SocialProvider(Protocol):
    """Per-symbol exotic 'hype' context as of a date (Truth-Social / Reddit-WSB, later).

    The YOLO sleeve's secondary feed. Same point-in-time contract: return only data that
    existed at-or-before ``as_of``. Slice 1 ships only the null default — the proxy hype the
    sleeve actually trades on is derived from price/volume in ``signals``, not from this feed.
    """

    def hype_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, HypeContext]:
        ...


class NullFilings:
    """No filings. The Phase-1 default."""

    def flags_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, FilingFlags]:
        return {}


class NullNews:
    """No news. The Phase-1 default."""

    def context_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, NewsContext]:
        return {}


class NullMacro:
    """No macro context. The Phase-1 default."""

    def values_as_of(self, as_of: date) -> Mapping[str, float]:
        return {}


class NullSocial:
    """No social/hype data. The Slice-1 default (the YOLO proxy needs no feed)."""

    def hype_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, HypeContext]:
        return {}
