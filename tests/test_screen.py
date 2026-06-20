"""Tests for the pure screen + the static universe provider.

All offline: synthetic bar frames drive the screen, so no network and no real key. Each test
asserts a knob *measurably* changes the output (the §4 acceptance criterion).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from core.config import ScreenConfig
from core.contracts import AccountState, NewsContext, SymbolMetadata
from data.frame import COLUMNS, INDEX_NAME
from ingest.snapshot import MarketSnapshot
from screen import StaticUniverseProvider, screen

_ACCOUNT = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)
_AS_OF = date(2024, 5, 20)


def _bars(close: float, volume: float, momentum: float, n: int = 80) -> pd.DataFrame:
    """A frame with flat raw ``close``/``volume`` and an ``adj_close`` that rises monotonically
    by ~``momentum`` across the window (so ROC is monotone in ``momentum``)."""
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n), name=INDEX_NAME)
    start_adj = close / (1.0 + momentum)
    adj = [start_adj + (close - start_adj) * (i / (n - 1)) for i in range(n)]
    data = {c: [close] * n for c in COLUMNS}
    data["volume"] = [volume] * n
    data["adj_close"] = adj
    return pd.DataFrame(data, index=idx)


# Default-config survivors plus three deliberate drop cases (penny / illiquid / short history).
def _snapshot() -> MarketSnapshot:
    prices = {
        "AAA": _bars(close=150.0, volume=1_000_000, momentum=0.30),  # IT
        "BBB": _bars(close=80.0, volume=2_000_000, momentum=0.10),   # Health Care, has news
        "CCC": _bars(close=40.0, volume=1_000_000, momentum=0.50),   # Energy
        "DDD": _bars(close=200.0, volume=1_000_000, momentum=0.20),  # IT
        "PENNY": _bars(close=2.0, volume=5_000_000, momentum=0.90),  # sub min-price
        "ILLIQ": _bars(close=100.0, volume=100, momentum=0.40),      # sub liquidity
        "SHORT": _bars(close=100.0, volume=1_000_000, momentum=0.40, n=30),  # too few bars
    }
    news = {"BBB": NewsContext(sentiment=0.5, headline_count=3)}
    return MarketSnapshot(as_of=_AS_OF, prices=prices, account=_ACCOUNT, news=news)


_METADATA = {
    "AAA": SymbolMetadata("AAA", "Alpha", "Information Technology"),
    "BBB": SymbolMetadata("BBB", "Beta", "Health Care"),
    "CCC": SymbolMetadata("CCC", "Gamma", "Energy"),
    "DDD": SymbolMetadata("DDD", "Delta", "Information Technology"),
    "PENNY": SymbolMetadata("PENNY", "Penny", "Energy"),
    "ILLIQ": SymbolMetadata("ILLIQ", "Illiquid", "Materials"),
    "SHORT": SymbolMetadata("SHORT", "Short", "Utilities"),
}


def _dropped(result) -> dict[str, str]:
    return dict(result.dropped)


def test_baseline_ranked_and_capped():
    result = screen(_snapshot(), _METADATA, ScreenConfig())
    assert len(result.candidates) <= ScreenConfig().max_candidates
    # The four healthy names survive; penny/illiquid/short are dropped.
    assert set(result.symbols) == {"AAA", "BBB", "CCC", "DDD"}
    # Ranks are contiguous 1..N.
    assert [c.rank for c in result.candidates] == list(range(1, len(result.candidates) + 1))


def test_min_price_floor_drops_subfloor():
    result = screen(_snapshot(), _METADATA, ScreenConfig(min_price=100.0))
    assert set(result.symbols) == {"AAA", "DDD"}  # 150, 200 survive; 80, 40 drop
    assert _dropped(result)["BBB"] == "below min price floor"
    assert _dropped(result)["CCC"] == "below min price floor"


def test_liquidity_floor_drops_illiquid():
    result = screen(_snapshot(), _METADATA, ScreenConfig(min_avg_dollar_volume=1e8))
    # CCC adv = 40 * 1e6 = 4e7 < 1e8 -> dropped; AAA/BBB/DDD clear it.
    assert "CCC" not in result.symbols
    assert _dropped(result)["CCC"] == "below liquidity floor"


def test_sectors_exclude_removes_sector():
    result = screen(_snapshot(), _METADATA, ScreenConfig(sectors_exclude=("Energy",)))
    assert "CCC" not in result.symbols
    assert _dropped(result)["CCC"] == "sector excluded"


def test_sectors_include_keeps_only_listed():
    result = screen(
        _snapshot(), _METADATA, ScreenConfig(sectors_include=("Information Technology",))
    )
    assert set(result.symbols) == {"AAA", "DDD"}
    assert _dropped(result)["BBB"] == "sector not included"


def test_watchlist_bypasses_sector_filter():
    cfg = ScreenConfig(sectors_exclude=("Energy",), watchlist=("CCC",))
    result = screen(_snapshot(), _METADATA, cfg)
    assert "CCC" in result.symbols
    # Pinned survivors take the priority slot.
    assert result.candidates[0].symbol == "CCC"


def test_watchlist_does_not_bypass_hard_floors():
    cfg = ScreenConfig(watchlist=("PENNY",))
    result = screen(_snapshot(), _METADATA, cfg)
    assert "PENNY" not in result.symbols
    assert _dropped(result)["PENNY"] == "below min price floor"


def test_momentum_ordering():
    result = screen(_snapshot(), _METADATA, ScreenConfig())
    # Higher ROC ranks first: CCC(.5) > AAA(.3) > DDD(.2) > BBB(.1).
    assert list(result.symbols) == ["CCC", "AAA", "DDD", "BBB"]


def test_relevance_weight_reorders():
    cfg = ScreenConfig(relevance_weight=10.0)
    result = screen(_snapshot(), _METADATA, cfg)
    # BBB's sentiment (0.5) * 10 dominates momentum, lifting it to the top.
    assert result.candidates[0].symbol == "BBB"


def test_max_candidates_truncates():
    result = screen(_snapshot(), _METADATA, ScreenConfig(max_candidates=2))
    assert len(result.candidates) == 2
    assert list(result.symbols) == ["CCC", "AAA"]  # top two by score


def test_determinism():
    cfg = ScreenConfig()
    first = screen(_snapshot(), _METADATA, cfg)
    second = screen(_snapshot(), _METADATA, cfg)
    assert first == second


def test_insufficient_history_dropped():
    result = screen(_snapshot(), _METADATA, ScreenConfig())
    assert "SHORT" not in result.symbols
    assert _dropped(result)["SHORT"] == "insufficient history"


def test_static_universe_provider_loads_seed():
    provider = StaticUniverseProvider()
    symbols = provider.symbols()
    assert len(symbols) >= 40
    assert len(set(symbols)) == len(symbols)  # no duplicates
    # Metadata is well-formed.
    for meta in provider.universe():
        assert meta.symbol and meta.name and meta.sector
        assert meta.asset_type in {"equity", "etf"}
    # metadata_for returns the requested subset and skips unknowns.
    subset = provider.metadata_for(["SPY", "AAPL", "NOPE"])
    assert set(subset) == {"SPY", "AAPL"}
    assert subset["SPY"].asset_type == "etf"
