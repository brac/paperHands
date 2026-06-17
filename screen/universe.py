"""The base universe — how the screen's candidate list is sourced.

§4 of the Phase-1 spec requires the universe list to be sourced explicitly. This slice ships
a committed static seed of liquid US large/mega-caps across sectors plus major ETFs
(``seed/universe.csv``), exposed through a ``UniverseProvider`` protocol so the source is a
swappable seam.

Limitation (documented deliberately): the static seed is survivorship-biased — it lists names
that are liquid *today* and silently omits anything that has since delisted, so a backtest run
over the seed cannot see companies that existed in the past but are gone now. The path to a
delisted-aware, point-in-time universe (e.g. a provider that reconstructs index membership as
of a date, or a vendor constituents feed) is the natural successor to this provider and slots
in behind the same protocol.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Protocol, get_args, runtime_checkable

from core.config import Settings
from core.contracts import AssetType, SymbolMetadata

_SEED_PATH = Path(__file__).parent / "seed" / "universe.csv"
_VALID_ASSET_TYPES = frozenset(get_args(AssetType))


@runtime_checkable
class UniverseProvider(Protocol):
    """Sources the base universe and its static reference metadata."""

    def universe(self) -> tuple[SymbolMetadata, ...]:
        """The full base universe as metadata rows."""
        ...

    def symbols(self) -> tuple[str, ...]:
        """Just the symbols in the base universe."""
        ...

    def metadata_for(self, symbols: Iterable[str]) -> dict[str, SymbolMetadata]:
        """Metadata for the requested symbols (silently skips unknown ones)."""
        ...

    def symbols_in_window(self, start: date, end: date) -> tuple[str, ...]:
        """Symbols that were listed at some point within ``[start, end]`` (point-in-time)."""
        ...


class StaticUniverseProvider:
    """A ``UniverseProvider`` backed by a committed static seed CSV.

    See the module docstring for the survivorship-bias limitation and the upgrade path.
    """

    def __init__(self, seed_path: Path = _SEED_PATH) -> None:
        self._seed_path = seed_path
        self._universe = self._load(seed_path)
        self._by_symbol = {m.symbol: m for m in self._universe}

    @staticmethod
    def _load(seed_path: Path) -> tuple[SymbolMetadata, ...]:
        rows: list[SymbolMetadata] = []
        with seed_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                asset_type = row["asset_type"].strip()
                if asset_type not in _VALID_ASSET_TYPES:
                    raise ValueError(
                        f"{seed_path.name}: invalid asset_type {asset_type!r} for "
                        f"{row['symbol']!r}"
                    )
                rows.append(
                    SymbolMetadata(
                        symbol=row["symbol"].strip(),
                        name=row["name"].strip(),
                        sector=row["sector"].strip(),
                        asset_type=asset_type,  # type: ignore[arg-type]  # validated above
                    )
                )
        return tuple(rows)

    def universe(self) -> tuple[SymbolMetadata, ...]:
        return self._universe

    def symbols(self) -> tuple[str, ...]:
        return tuple(m.symbol for m in self._universe)

    def metadata_for(self, symbols: Iterable[str]) -> dict[str, SymbolMetadata]:
        return {s: self._by_symbol[s] for s in symbols if s in self._by_symbol}

    def symbols_in_window(self, start: date, end: date) -> tuple[str, ...]:
        """The static seed carries no listing dates, so every name is always eligible."""
        del start, end
        return self.symbols()


def build_universe_provider(settings: Settings) -> UniverseProvider:
    """Composition-root factory; selects the universe source from config (default static seed)."""
    if settings.universe.source == "tiingo":
        from screen.tiingo_universe import build_tiingo_universe_provider

        return build_tiingo_universe_provider(settings)
    return StaticUniverseProvider()
