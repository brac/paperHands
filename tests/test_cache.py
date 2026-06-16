"""Tests for the parquet bar cache: round-trip, merge/dedupe, coverage."""

from __future__ import annotations

from datetime import date

import pandas as pd

from data.cache import ParquetBarCache
from data.frame import COLUMNS, INDEX_NAME


def _frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name=INDEX_NAME)
    data = {c: [float(i + 1) for i in range(len(dates))] for c in COLUMNS}
    return pd.DataFrame(data, index=idx)


def test_round_trip_preserves_data(tmp_path):
    cache = ParquetBarCache(tmp_path)
    df = _frame(["2024-01-02", "2024-01-03", "2024-01-04"])
    cache.store("AAA", df, date(2024, 1, 1), date(2024, 1, 4))
    loaded = cache.load("AAA")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded, df)


def test_load_missing_returns_none(tmp_path):
    assert ParquetBarCache(tmp_path).load("NOPE") is None


def test_merge_dedupes_by_date_new_wins(tmp_path):
    cache = ParquetBarCache(tmp_path)
    first = _frame(["2024-01-02", "2024-01-03"])
    cache.store("AAA", first, date(2024, 1, 2), date(2024, 1, 3))

    # Overlapping store: 01-03 repeats (with different values), 01-04 is new.
    second = _frame(["2024-01-03", "2024-01-04"])
    second.loc[second.index[0], "close"] = 999.0
    merged = cache.store("AAA", second, date(2024, 1, 3), date(2024, 1, 4))

    assert list(merged.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert merged.loc[pd.Timestamp("2024-01-03"), "close"] == 999.0  # new row wins


def test_coverage_union(tmp_path):
    cache = ParquetBarCache(tmp_path)
    cache.store("AAA", _frame(["2024-01-02"]), date(2024, 1, 1), date(2024, 1, 10))
    cache.store("AAA", _frame(["2024-02-01"]), date(2024, 1, 20), date(2024, 2, 5))
    # Union of [Jan1,Jan10] and [Jan20,Feb5] brackets a sub-range within the hull.
    assert cache.covers("AAA", date(2024, 1, 5), date(2024, 2, 1))
    # ...but not beyond the hull.
    assert not cache.covers("AAA", date(2024, 1, 5), date(2024, 3, 1))


def test_covers_false_when_uncached(tmp_path):
    assert not ParquetBarCache(tmp_path).covers("AAA", date(2024, 1, 1), date(2024, 1, 2))
