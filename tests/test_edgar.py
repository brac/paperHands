"""Tests for the EDGAR filings provider — point-in-time flags, recency, mapping, caching.

All offline: the single HTTP call is injected, so a fake returns canned ticker/submissions
JSON and counts requests. No network, no ``requests`` import exercised.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from ingest.edgar import _TICKERS_URL, EdgarFilingsProvider

_TICKERS = {
    "0": {"cik_str": 111, "ticker": "AAA", "title": "Alpha"},
    "1": {"cik_str": 222, "ticker": "BBB", "title": "Beta"},
}


def _submissions(forms: list[str], dates: list[str]) -> dict[str, Any]:
    return {"filings": {"recent": {"form": forms, "filingDate": dates}}}


class _FakeFetch:
    """Returns canned JSON by URL and records every requested URL."""

    def __init__(self, subs_by_cik: dict[str, dict[str, Any]]) -> None:
        self._subs = subs_by_cik
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Any:
        assert "User-Agent" in headers and headers["User-Agent"]
        self.urls.append(url)
        if url == _TICKERS_URL:
            return _TICKERS
        for cik, payload in self._subs.items():
            if cik in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")


def _provider(fetch: _FakeFetch, tmp_path, *, recency_days: int = 5) -> EdgarFilingsProvider:
    return EdgarFilingsProvider(
        user_agent="paperhands test@example.com",
        recency_days=recency_days,
        cache_dir=tmp_path,
        fetch=fetch,
    )


def test_recent_8k_flag_set_old_form4_ignored(tmp_path):
    fetch = _FakeFetch({"0000000111": _submissions(["8-K", "4"], ["2024-05-01", "2024-04-01"])})
    flags = _provider(fetch, tmp_path).flags_as_of(["AAA"], date(2024, 5, 3))
    assert flags["AAA"].recent_8k is True          # 8-K on 05-01 within 5 days of 05-03
    assert flags["AAA"].recent_insider_buy is False  # Form 4 on 04-01 is outside the window


def test_form4_maps_to_insider_buy(tmp_path):
    fetch = _FakeFetch({"0000000111": _submissions(["4"], ["2024-05-02"])})
    flags = _provider(fetch, tmp_path).flags_as_of(["AAA"], date(2024, 5, 3))
    assert flags["AAA"].recent_insider_buy is True
    assert flags["AAA"].recent_8k is False


def test_point_in_time_excludes_future_filing(tmp_path):
    # The 8-K is dated AFTER the as_of -> it must never set a flag (no look-ahead).
    fetch = _FakeFetch({"0000000111": _submissions(["8-K"], ["2024-05-01"])})
    flags = _provider(fetch, tmp_path).flags_as_of(["AAA"], date(2024, 4, 30))
    assert "AAA" not in flags


def test_recency_window_boundary(tmp_path):
    # window_start = as_of - 5 = 2024-05-01; a filing exactly on the boundary counts.
    # Distinct cache dirs so the two providers don't share the on-disk filings cache.
    as_of = date(2024, 5, 6)
    on_edge = _FakeFetch({"0000000111": _submissions(["8-K"], ["2024-05-01"])})
    assert _provider(on_edge, tmp_path / "edge").flags_as_of(["AAA"], as_of)["AAA"].recent_8k
    just_outside = _FakeFetch({"0000000111": _submissions(["8-K"], ["2024-04-30"])})
    assert "AAA" not in _provider(just_outside, tmp_path / "out").flags_as_of(["AAA"], as_of)


def test_unknown_ticker_is_omitted(tmp_path):
    fetch = _FakeFetch({})
    assert _provider(fetch, tmp_path).flags_as_of(["ZZZ"], date(2024, 5, 3)) == {}


def test_fetches_each_symbol_once(tmp_path):
    fetch = _FakeFetch({"0000000111": _submissions(["8-K"], ["2024-05-01"])})
    provider = _provider(fetch, tmp_path)
    provider.flags_as_of(["AAA"], date(2024, 5, 3))
    provider.flags_as_of(["AAA"], date(2024, 5, 3))
    assert fetch.urls.count(_TICKERS_URL) == 1                              # ticker map once
    assert sum("0000000111" in u for u in fetch.urls) == 1                  # submissions once


def test_empty_user_agent_rejected(tmp_path):
    with pytest.raises(ValueError, match="user_agent"):
        EdgarFilingsProvider(user_agent="", recency_days=5, cache_dir=tmp_path)
