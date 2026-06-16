"""Tests for the ingest layer — snapshot assembly, as-of correctness, no-look-ahead guard.

All offline: a fake DataProvider supplies bars, so no network and no real key.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from core.contracts import AccountState
from data.frame import COLUMNS, INDEX_NAME
from ingest import (
    LookAheadError,
    NullFilings,
    NullMacro,
    NullNews,
    SnapshotAssembler,
)


def _bars(dates: list[date]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name=INDEX_NAME)
    data = {c: [float(i + 1) for i in range(len(dates))] for c in COLUMNS}
    return pd.DataFrame(data, index=idx)


class FakeProvider:
    """Records its calls and returns business-day bars, capping at as_of by default."""

    def __init__(self, *, honor_as_of: bool = True) -> None:
        self.calls: list[tuple[str, date, date, date | None]] = []
        self._honor_as_of = honor_as_of

    def get_daily_bars(self, symbol, start, end, *, as_of=None):
        self.calls.append((symbol, start, end, as_of))
        cap = end if as_of is None else min(end, as_of)
        days = [d.date() for d in pd.bdate_range(start, end)]
        if self._honor_as_of:
            days = [d for d in days if d <= cap]
        else:
            # Misbehaving: leak a bar dated well after the requested window / as_of.
            days.append(end + timedelta(days=10))
        return _bars(days)


_ACCOUNT = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)
_AS_OF = date(2024, 3, 28)


def test_as_of_correctness():
    assembler = SnapshotAssembler(FakeProvider(), history_days=600)
    snap = assembler.assemble(["AAA", "BBB"], _AS_OF, _ACCOUNT)
    for df in snap.prices.values():
        assert df.index.max().date() <= _AS_OF


def test_no_look_ahead_guard_rejects_future_bar():
    # A misbehaving provider that ignores as_of and returns future-dated bars.
    assembler = SnapshotAssembler(FakeProvider(honor_as_of=False), history_days=5)
    with pytest.raises(LookAheadError):
        assembler.assemble(["AAA"], _AS_OF, _ACCOUNT)


def test_snapshot_shape_and_account_passthrough():
    assembler = SnapshotAssembler(FakeProvider())
    snap = assembler.assemble(["AAA", "BBB"], _AS_OF, _ACCOUNT)
    assert set(snap.symbols) == {"AAA", "BBB"}
    assert snap.account is _ACCOUNT
    assert snap.as_of == _AS_OF
    # Null feeds yield empty secondary slots.
    assert snap.filings == {}
    assert snap.news == {}
    assert snap.macro == {}


def test_history_window_passed_to_provider():
    provider = FakeProvider()
    assembler = SnapshotAssembler(provider, history_days=600)
    assembler.assemble(["AAA"], _AS_OF, _ACCOUNT)
    symbol, start, end, as_of = provider.calls[0]
    assert start == _AS_OF - timedelta(days=600)
    assert end == _AS_OF
    assert as_of == _AS_OF


def test_null_feeds_return_empty():
    assert NullFilings().flags_as_of(["AAA"], _AS_OF) == {}
    assert NullNews().context_as_of(["AAA"], _AS_OF) == {}
    assert NullMacro().values_as_of(_AS_OF) == {}
