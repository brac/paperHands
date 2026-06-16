"""Tests for the Tiingo data provider — all offline (the HTTP fetch is injected/stubbed)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

from data.cache import ParquetBarCache
from data.frame import COLUMNS, REQUIRED_COLUMNS
from data.tiingo import TiingoProvider, _rows_to_frame

# Canned span used by the fake fetch.
_SPAN_START = date(2024, 1, 1)
_SPAN_END = date(2024, 3, 31)


def _tiingo_rows(start: date = _SPAN_START, end: date = _SPAN_END) -> list[dict[str, Any]]:
    """Deterministic Tiingo-shaped daily rows for business days in [start, end]."""
    days = pd.bdate_range(start, end)
    rows: list[dict[str, Any]] = []
    for i, d in enumerate(days):
        base = 100.0 + i
        rows.append({
            "date": d.strftime("%Y-%m-%dT00:00:00.000Z"),
            "open": base, "high": base + 1, "low": base - 1, "close": base + 0.5,
            "volume": 1_000_000 + i,
            "adjOpen": base, "adjHigh": base + 1, "adjLow": base - 1,
            "adjClose": base + 0.5, "adjVolume": 1_000_000 + i,
            "divCash": 0.0, "splitFactor": 1.0,
        })
    return rows


def _counting_fetch(rows: list[dict[str, Any]]) -> tuple[Any, list[int]]:
    calls = [0]

    def fetch(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        calls[0] += 1
        return rows

    return fetch, calls


def _raising_fetch(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    raise AssertionError("network fetch invoked — should have been served from cache")


# --------------------------------------------------------------------------------------
def test_second_run_reads_from_cache_without_network(tmp_path):
    rows = _tiingo_rows()
    fetch, calls = _counting_fetch(rows)

    # First run: warms the cache (one fetch).
    p1 = TiingoProvider("KEY", ParquetBarCache(tmp_path), fetch=fetch)
    bars1 = p1.get_daily_bars("AAPL", _SPAN_START, _SPAN_END)
    assert calls[0] == 1
    assert len(bars1) == len(pd.bdate_range(_SPAN_START, _SPAN_END))
    assert (tmp_path / "tiingo" / "AAPL.parquet").exists()

    # Second run (fresh provider + cache instance, same dir): must not hit the network.
    p2 = TiingoProvider("KEY", ParquetBarCache(tmp_path), fetch=_raising_fetch)
    bars2 = p2.get_daily_bars("AAPL", _SPAN_START, _SPAN_END)
    pd.testing.assert_frame_equal(bars1, bars2)


def test_as_of_caps_returned_data(tmp_path):
    fetch, _ = _counting_fetch(_tiingo_rows())
    provider = TiingoProvider("KEY", ParquetBarCache(tmp_path), fetch=fetch)
    as_of = date(2024, 2, 15)
    bars = provider.get_daily_bars("AAPL", _SPAN_START, _SPAN_END, as_of=as_of)
    assert len(bars) > 0
    assert bars.index.max().date() <= as_of


def test_date_range_is_sliced(tmp_path):
    fetch, _ = _counting_fetch(_tiingo_rows())
    provider = TiingoProvider("KEY", ParquetBarCache(tmp_path), fetch=fetch)
    bars = provider.get_daily_bars("AAPL", date(2024, 2, 1), date(2024, 2, 29))
    assert bars.index.min().date() >= date(2024, 2, 1)
    assert bars.index.max().date() <= date(2024, 2, 29)


def test_as_of_before_start_returns_empty(tmp_path):
    fetch, calls = _counting_fetch(_tiingo_rows())
    provider = TiingoProvider("KEY", ParquetBarCache(tmp_path), fetch=fetch)
    bars = provider.get_daily_bars("AAPL", date(2024, 2, 1), date(2024, 2, 29),
                                   as_of=date(2024, 1, 15))
    assert bars.empty
    assert calls[0] == 0  # no fetch needed for an empty window


def test_missing_key_raises_on_fetch(tmp_path):
    provider = TiingoProvider(None, ParquetBarCache(tmp_path), fetch=_raising_fetch)
    with pytest.raises(RuntimeError, match="TIINGO_API_KEY"):
        provider.get_daily_bars("AAPL", _SPAN_START, _SPAN_END)


def test_rows_parse_to_canonical_frame():
    df = _rows_to_frame(_tiingo_rows())
    assert list(df.columns) == COLUMNS
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert not df[REQUIRED_COLUMNS].isna().to_numpy().any()
    # adjClose mapped to adj_close, numeric.
    assert df["adj_close"].dtype.kind == "f"


def test_empty_rows_parse_to_empty_frame():
    df = _rows_to_frame([])
    assert df.empty
    assert list(df.columns) == COLUMNS
