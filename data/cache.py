"""Local parquet cache for fetched daily bars.

One parquet file per symbol under ``{cache_dir}/tiingo/{SYMBOL}.parquet``, plus a small
``{SYMBOL}.coverage.json`` sidecar recording the **fetched calendar ranges**. Coverage is
tracked separately from the bar data because the data alone can't distinguish "no bar on
Jan 1 (a holiday)" from "Jan 1 was never fetched" — so a calendar-range request would never
register as a hit if we keyed off the first/last bar date. The sidecar is the authoritative
answer to "do we already have this span?", which is what guarantees a second identical run
reads from cache without network.

Coverage is a list of **merged intervals**, not a single hull: fetching two *disjoint* date
ranges (e.g. a 2020 window and a 2024 window) must NOT make the gap between them look cached.
A request is a hit only if a single contiguous interval brackets it.

``store`` merges new rows with existing ones (deduping by date, new rows win) and unions the
fetched interval into the coverage list, so repeated backtests over overlapping ranges
converge to one complete history.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data.frame import INDEX_NAME, validate_bars


class ParquetBarCache:
    """Per-symbol parquet cache of daily bars + a fetched-range coverage sidecar."""

    def __init__(self, cache_dir: str | Path, namespace: str = "tiingo") -> None:
        self._dir = Path(cache_dir) / namespace
        # In-memory memo of loaded frames, keyed by upper-cased symbol. A backtest re-reads
        # each symbol every step; this turns thousands of parquet parses into one per run.
        # Frames are treated as read-only everywhere (callers slice, never mutate in place).
        self._memo: dict[str, pd.DataFrame] = {}

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.parquet"

    def _coverage_path(self, symbol: str) -> Path:
        return self._dir / f"{symbol.upper()}.coverage.json"

    def load(self, symbol: str) -> pd.DataFrame | None:
        """Return the cached bar frame for ``symbol``, or ``None`` if not cached."""
        key = symbol.upper()
        memoized = self._memo.get(key)
        if memoized is not None:
            return memoized
        path = self._path(symbol)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        df.index = pd.DatetimeIndex(df.index, name=INDEX_NAME)
        frame = validate_bars(df.sort_index())
        self._memo[key] = frame
        return frame

    def store(
        self, symbol: str, df: pd.DataFrame, fetched_start: date, fetched_end: date
    ) -> pd.DataFrame:
        """Merge ``df`` into the cache, union the coverage span, and persist.

        ``fetched_start``/``fetched_end`` are the *requested* calendar range (not the data
        min/max) so coverage reflects what was actually asked of the API.
        """
        existing = self.load(symbol)
        if existing is not None and len(existing):
            merged = pd.concat([existing, df])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = df.sort_index()
        merged = validate_bars(merged)

        self._dir.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(self._path(symbol))
        self._memo[symbol.upper()] = merged  # keep the memo consistent with disk

        intervals = _merge_intervals([*self._intervals(symbol), (fetched_start, fetched_end)])
        self._coverage_path(symbol).write_text(
            json.dumps([{"start": lo.isoformat(), "end": hi.isoformat()} for lo, hi in intervals])
        )
        return merged

    def covers(self, symbol: str, start: date, end: date) -> bool:
        """True if a single fetched interval brackets [start, end] (a hit needs no fetch)."""
        return any(lo <= start and hi >= end for lo, hi in self._intervals(symbol))

    def _intervals(self, symbol: str) -> list[tuple[date, date]]:
        path = self._coverage_path(symbol)
        if not path.exists():
            return []
        rec = json.loads(path.read_text())
        # Back-compat: an old single-hull ``{start, end}`` is read as one interval.
        rows = [rec] if isinstance(rec, dict) else rec
        return [(date.fromisoformat(r["start"]), date.fromisoformat(r["end"])) for r in rows]


def _merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Sort and merge overlapping/adjacent (touching within a day) date intervals."""
    ordered = sorted(intervals)
    merged: list[tuple[date, date]] = []
    for lo, hi in ordered:
        if merged and lo <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged
