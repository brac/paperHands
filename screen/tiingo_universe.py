"""A survivorship-aware small-cap universe from Tiingo's supported-tickers list.

Tiingo's ``supported_tickers`` file lists ~22k US-exchange common stocks *including ~7.7k that
have delisted*, each with a listing ``[startDate, endDate]``. That makes a point-in-time,
delisted-inclusive universe possible on the data we already pay for: ``symbols_in_window`` only
returns names that were actually listed during a backtest window, and the delisted names let the
engine "buy" companies that later went to zero — the survivorship-bias fix the static seed lacks.

The list is fetched once (injected ``fetch`` seam, like ``data/tiingo.py``) and cached on disk.
Selection to ``max_symbols`` is deterministic and **survivorship-neutral** — a stable hash over
the *full* active+delisted pool — so delisted names are kept in proportion, never filtered out.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.contracts import SymbolMetadata

if TYPE_CHECKING:
    from core.config import Settings

# Returns the raw Tiingo supported-ticker rows (dicts with ticker/exchange/assetType/dates).
FetchFn = Callable[[], list[dict[str, Any]]]

_TICKERS_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"


def _default_fetch() -> list[dict[str, Any]]:
    """Download + unzip Tiingo's supported-tickers CSV (the only network/IO path)."""
    import csv as _csv
    import io
    import zipfile

    import requests

    blob = requests.get(_TICKERS_URL, timeout=120).content
    archive = zipfile.ZipFile(io.BytesIO(blob))
    with archive.open(archive.namelist()[0]) as fh:
        return list(_csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class _Listing:
    symbol: str
    start: date
    end: date | None  # None = still listed


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_common_ticker(ticker: str) -> bool:
    """Keep plausible common stocks; drop preferreds/warrants/units/when-issued.

    Heuristic (no clean classifier in the feed): letters only (drops ``BRK-B`` and digit/
    when-issued rows), max 5 chars, and not a 5-letter ticker ending in U/W/R — almost always
    a SPAC unit/warrant/right. Imperfect (rare false drops), but it keeps the universe tradeable.
    """
    if not ticker.isalpha() or len(ticker) > 5:
        return False
    return not (len(ticker) == 5 and ticker[-1] in {"U", "W", "R"})


class TiingoUniverseProvider:
    """Point-in-time small-cap universe (active + delisted) from Tiingo supported-tickers."""

    def __init__(
        self,
        *,
        max_symbols: int,
        exchanges: tuple[str, ...],
        cache_dir: str | Path,
        fetch: FetchFn = _default_fetch,
    ) -> None:
        self._cache = Path(cache_dir) / "universe" / "tiingo_stocks.json"
        rows = self._load_rows(fetch)
        self._listings = self._select(rows, exchanges, max_symbols)
        self._meta = {
            li.symbol: SymbolMetadata(li.symbol, li.symbol, sector="", asset_type="equity")
            for li in self._listings
        }

    # -- loading / caching ------------------------------------------------------------
    def _load_rows(self, fetch: FetchFn) -> list[dict[str, Any]]:
        if self._cache.exists():
            return json.loads(self._cache.read_text(encoding="utf-8"))
        rows = [r for r in fetch() if r.get("assetType") == "Stock"]
        minimal = [
            {"ticker": r.get("ticker", ""), "exchange": r.get("exchange", ""),
             "startDate": r.get("startDate", ""), "endDate": r.get("endDate", "")}
            for r in rows
        ]
        self._cache.parent.mkdir(parents=True, exist_ok=True)
        self._cache.write_text(json.dumps(minimal), encoding="utf-8")
        return minimal

    def _select(
        self, rows: list[dict[str, Any]], exchanges: tuple[str, ...], max_symbols: int
    ) -> tuple[_Listing, ...]:
        wanted = set(exchanges)
        by_symbol: dict[str, _Listing] = {}
        for row in rows:
            ticker = str(row.get("ticker", "")).upper()
            if row.get("exchange") not in wanted or not _is_common_ticker(ticker):
                continue
            start = _parse_date(row.get("startDate")) or date.min
            end = _parse_date(row.get("endDate"))
            existing = by_symbol.get(ticker)
            # Dedupe relisted tickers to the widest [start, end] span.
            if existing is None:
                by_symbol[ticker] = _Listing(ticker, start, end)
            else:
                merged_end = None if existing.end is None or end is None else max(existing.end, end)
                by_symbol[ticker] = _Listing(ticker, min(existing.start, start), merged_end)
        # Survivorship-neutral, deterministic pick: stable hash over the whole pool.
        ordered = sorted(by_symbol.values(), key=lambda li: _hash_key(li.symbol))
        return tuple(sorted(ordered[:max_symbols], key=lambda li: li.symbol))

    # -- UniverseProvider -------------------------------------------------------------
    def universe(self) -> tuple[SymbolMetadata, ...]:
        return tuple(self._meta[li.symbol] for li in self._listings)

    def symbols(self) -> tuple[str, ...]:
        return tuple(li.symbol for li in self._listings)

    def metadata_for(self, symbols: Iterable[str]) -> dict[str, SymbolMetadata]:
        return {s: self._meta[s] for s in symbols if s in self._meta}

    def symbols_in_window(self, start: date, end: date) -> tuple[str, ...]:
        """Names whose listing span overlaps ``[start, end]`` (point-in-time membership)."""
        return tuple(
            li.symbol for li in self._listings
            if li.start <= end and (li.end is None or li.end >= start)
        )


def _hash_key(symbol: str) -> int:
    """Stable (cross-process) hash for deterministic selection — ``hash()`` is salted."""
    return int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)


def build_tiingo_universe_provider(
    settings: Settings, *, fetch: FetchFn = _default_fetch
) -> TiingoUniverseProvider:
    """Composition root: build the provider from ``UniverseConfig``."""
    return TiingoUniverseProvider(
        max_symbols=settings.universe.max_symbols,
        exchanges=settings.universe.exchanges,
        cache_dir=settings.universe.cache_dir,
        fetch=fetch,
    )
