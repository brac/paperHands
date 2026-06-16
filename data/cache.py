"""Local parquet cache for fetched daily bars.

One parquet file per symbol under ``{cache_dir}/tiingo/{SYMBOL}.parquet``, plus a small
``{SYMBOL}.coverage.json`` sidecar recording the **fetched calendar range**. Coverage is
tracked separately from the bar data because the data alone can't distinguish "no bar on
Jan 1 (a holiday)" from "Jan 1 was never fetched" — so a calendar-range request would never
register as a hit if we keyed off the first/last bar date. The sidecar is the authoritative
answer to "do we already have this span?", which is what guarantees a second identical run
reads from cache without network.

``store`` merges new rows with existing ones (deduping by date, new rows win) and unions the
coverage span, so repeated backtests over overlapping ranges converge to one complete history.
"""

from __future__ import annotations

import json
from datetime import date
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

        lo, hi = self._coverage(symbol)
        new_lo = fetched_start if lo is None else min(lo, fetched_start)
        new_hi = fetched_end if hi is None else max(hi, fetched_end)
        self._coverage_path(symbol).write_text(
            json.dumps({"start": new_lo.isoformat(), "end": new_hi.isoformat()})
        )
        return merged

    def covers(self, symbol: str, start: date, end: date) -> bool:
        """True if the fetched coverage span brackets [start, end] (a hit needs no fetch)."""
        lo, hi = self._coverage(symbol)
        if lo is None or hi is None:
            return False
        return lo <= start and hi >= end

    def _coverage(self, symbol: str) -> tuple[date | None, date | None]:
        path = self._coverage_path(symbol)
        if not path.exists():
            return None, None
        rec = json.loads(path.read_text())
        return date.fromisoformat(rec["start"]), date.fromisoformat(rec["end"])
