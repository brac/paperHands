"""Tiingo EOD historical data provider (point-in-time correct), with a parquet cache.

Tiingo is the default per ``docs/PROJECT.md`` (cheap, long EOD history). The single HTTP
call is injected (``fetch=``) so tests stub it with canned JSON — the same dependency-
injection discipline the risk gate and (later) the LLM client follow; no network in tests.

**Survivorship-bias limitation (documented, not solved):** Tiingo's EOD universe is
primarily currently-listed names, so a naive symbol list omits companies that delisted
during the window — backtests over such a list are optimistically biased. Tiingo offers
*some* delisted coverage; Polygon offers deeper coverage and is the swappable alternative
left for a later slice. At hobby scale this is mitigated (provider choice) rather than
fully solved; keep it in mind when interpreting results.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from data.cache import ParquetBarCache
from data.frame import COLUMNS, INDEX_NAME, validate_bars

# A fetch returns the raw Tiingo JSON rows (list of dicts) for a symbol/date range.
FetchFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]

# Tiingo daily field -> canonical column.
_FIELD_MAP = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "adjOpen": "adj_open",
    "adjHigh": "adj_high",
    "adjLow": "adj_low",
    "adjClose": "adj_close",
    "adjVolume": "adj_volume",
}


def _default_fetch(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Real network fetch — the only place this module touches the wire."""
    import requests

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"unexpected Tiingo response shape: {type(data).__name__}")
    return data


def _rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Parse Tiingo daily JSON rows into a canonical, validated bar frame."""
    if not rows:
        from data.frame import empty_bars

        return empty_bars()
    df = pd.DataFrame(rows)
    # Tiingo dates are ISO timestamps (e.g. "2020-01-02T00:00:00.000Z"); keep date only.
    index = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
    out = pd.DataFrame(index=pd.DatetimeIndex(index, name=INDEX_NAME))
    for src, dst in _FIELD_MAP.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce").to_numpy() if src in df else pd.NA
    out = out[COLUMNS].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return validate_bars(out)


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


class TiingoProvider:
    """Swappable ``DataProvider``: cached, as-of-correct daily bars from Tiingo."""

    def __init__(
        self,
        api_key: str | None,
        cache: ParquetBarCache,
        *,
        base_url: str = "https://api.tiingo.com",
        fetch: FetchFn = _default_fetch,
    ) -> None:
        self._api_key = api_key
        self._cache = cache
        self._base_url = base_url.rstrip("/")
        self._fetch = fetch

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Daily bars for ``symbol`` in [start, end], capped at ``as_of`` (default end).

        Point-in-time guarantee: no returned row postdates ``as_of``. Served from the
        parquet cache when it covers the window; otherwise fetched once, cached, and sliced.
        """
        eff_end = end if as_of is None else min(end, as_of)
        if eff_end < start:
            from data.frame import empty_bars

            return empty_bars()

        if self._cache.covers(symbol, start, eff_end):
            cached = self._cache.load(symbol)
            assert cached is not None  # covers() guarantees presence
            return self._slice(cached, start, eff_end)

        rows = self._fetch_rows(symbol, start, end)
        merged = self._cache.store(symbol, _rows_to_frame(rows), start, end)
        return self._slice(merged, start, eff_end)

    def _fetch_rows(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        if not self._api_key:
            raise RuntimeError(
                "TIINGO_API_KEY is not set; cannot fetch uncached data for "
                f"{symbol}. Set it in .env or warm the cache."
            )
        url = f"{self._base_url}/tiingo/daily/{symbol}/prices"
        params: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "format": "json",
            "token": self._api_key,
        }
        return self._fetch(url, params)

    @staticmethod
    def _slice(df: pd.DataFrame, start: date, eff_end: date) -> pd.DataFrame:
        mask = (df.index >= _ts(start)) & (df.index <= _ts(eff_end))
        return validate_bars(df.loc[mask])
