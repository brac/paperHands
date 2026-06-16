"""Historical/live data provider layer (point-in-time correct).

Public surface: the ``DataProvider`` interface, the Tiingo implementation, and the
``build_data_provider`` composition helper. The default Tiingo provider caches fetched
history to parquet; a Polygon implementation (deeper delisted coverage) is left for later.
"""

from data.base import DataProvider
from data.factory import build_data_provider
from data.tiingo import TiingoProvider

__all__ = ["DataProvider", "TiingoProvider", "build_data_provider"]
