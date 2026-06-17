"""Tests for the Tiingo small-cap universe — filtering, dedupe, point-in-time, determinism.

Offline: the supported-tickers fetch is injected with canned rows; no network, no zip.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from screen.tiingo_universe import TiingoUniverseProvider


def _row(ticker: str, exchange: str, start: str, end: str, asset: str = "Stock") -> dict[str, Any]:
    return {"ticker": ticker, "exchange": exchange, "assetType": asset,
            "startDate": start, "endDate": end}


_ROWS = [
    _row("AAA", "NYSE", "2010-01-01", ""),            # active, in range
    _row("DEAD", "NASDAQ", "2012-01-01", "2016-06-30"),  # delisted before a 2018 window
    _row("IPO", "NASDAQ", "2022-01-01", ""),          # listed only from 2022
    _row("OLDR", "NYSE", "2009-01-01", "2024-01-01"),  # long-lived
    _row("FUND", "NASDAQ", "2010-01-01", "", asset="ETF"),  # not a Stock -> dropped
    _row("OTCX", "OTC", "2010-01-01", ""),            # wrong exchange -> dropped
    _row("ABCDW", "NASDAQ", "2010-01-01", ""),        # 5-char... but warrant 'W'? still alnum/<=5
    _row("BRK-B", "NYSE", "2010-01-01", ""),          # non-alnum -> dropped
]


def _provider(rows: list[dict[str, Any]], *, max_symbols: int = 100, tmp_path=None
              ) -> TiingoUniverseProvider:  # noqa: ANN001
    return TiingoUniverseProvider(
        max_symbols=max_symbols,
        exchanges=("NYSE", "NASDAQ", "NYSE MKT", "AMEX"),
        cache_dir=tmp_path,
        fetch=lambda: list(rows),
    )


def test_filters_to_us_common_stocks(tmp_path):
    syms = set(_provider(_ROWS, tmp_path=tmp_path).symbols())
    assert {"AAA", "DEAD", "IPO", "OLDR"} <= syms
    assert "FUND" not in syms   # ETF
    assert "OTCX" not in syms   # wrong exchange
    assert "BRK-B" not in syms  # non-alphanumeric


def test_point_in_time_membership(tmp_path):
    p = _provider(_ROWS, tmp_path=tmp_path)
    window = p.symbols_in_window(date(2018, 1, 1), date(2018, 12, 31))
    assert "AAA" in window and "OLDR" in window  # listed across 2018
    assert "DEAD" not in window                  # delisted 2016
    assert "IPO" not in window                   # not listed until 2022
    # The IPO name appears once its window opens.
    assert "IPO" in p.symbols_in_window(date(2022, 6, 1), date(2023, 1, 1))


def test_delisted_names_are_retained(tmp_path):
    # The survivorship fix: a delisted name must be selectable, not filtered away.
    assert "DEAD" in _provider(_ROWS, tmp_path=tmp_path).symbols()


def test_max_symbols_cap_is_deterministic(tmp_path):
    import string
    up = string.ascii_uppercase
    rows = [_row("T" + up[i // 26] + up[i % 26], "NYSE", "2010-01-01", "") for i in range(50)]
    a = _provider(rows, max_symbols=10, tmp_path=tmp_path / "a").symbols()
    b = _provider(rows, max_symbols=10, tmp_path=tmp_path / "b").symbols()
    assert len(a) == 10 and a == b  # capped and stable across instances


def test_dedupes_relisted_ticker_to_widest_span(tmp_path):
    rows = [
        _row("REL", "NYSE", "2015-01-01", "2017-01-01"),
        _row("REL", "NYSE", "2019-01-01", ""),
    ]
    p = _provider(rows, tmp_path=tmp_path)
    assert p.symbols() == ("REL",)
    assert "REL" in p.symbols_in_window(date(2016, 1, 1), date(2016, 6, 1))  # earlier span
    assert "REL" in p.symbols_in_window(date(2020, 1, 1), date(2020, 6, 1))  # later span
