"""Composition root for the data provider.

Constructs the configured provider + cache from ``Settings`` and returns it as a
``DataProvider``. Lives here (not in a pure module) because providers do I/O; pure modules
receive the constructed provider via injection, never import it.
"""

from __future__ import annotations

from core.config import Settings
from data.base import DataProvider
from data.cache import ParquetBarCache
from data.tiingo import TiingoProvider


def build_data_provider(settings: Settings) -> DataProvider:
    """Build the provider named by ``settings.data.provider``.

    The API key may be absent at construction time (so offline/no-key runs work); a fetch
    of uncached data raises a clear error if it is still missing.
    """
    name = settings.data.provider
    if name == "tiingo":
        cache = ParquetBarCache(settings.data.cache_dir, namespace="tiingo")
        return TiingoProvider(
            api_key=settings.tiingo_api_key,
            cache=cache,
            base_url=settings.data.tiingo_base_url,
        )
    raise ValueError(f"unknown data provider: {name!r}")
