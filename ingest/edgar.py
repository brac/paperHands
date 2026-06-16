"""SEC EDGAR filings provider — point-in-time recent-8-K / Form-4 flags.

Implements the ``FilingsProvider`` Protocol against SEC's free EDGAR endpoints. A filing's
``filingDate`` is when it became public and is never revised, so point-in-time correctness is
honest by construction: ``flags_as_of`` only counts filings dated **at or before** ``as_of``.

Each symbol's full recent-filings list is fetched **once** and cached (in memory + on disk), so
every as-of query during a backtest is answered from the cache by date-filtering — a whole run
costs ~one EDGAR request per symbol (well under SEC's 10 req/s), not one per bar. The single
HTTP call is injected (``fetch=``, mirroring ``data/tiingo.py``) so tests run with no network.

The on-disk cache stamps the wall-clock date it was fetched; a query for an ``as_of`` newer than
that stamp (i.e. the live cycle on a new day) re-fetches so today's filings aren't missed, while
backtests over past dates always hit the cache.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.contracts import FilingFlags

if TYPE_CHECKING:
    from core.config import Settings

# fetch(url, headers) -> parsed JSON. Injected so tests stub it and ``requests`` stays lazy.
FetchFn = Callable[[str, dict[str, str]], Any]

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FORM_8K = "8-K"
_FORM_4 = "4"


def _default_fetch(url: str, headers: dict[str, str]) -> Any:
    """Real network fetch — the only place this module touches the wire."""
    import requests

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


@dataclass(frozen=True, slots=True)
class _Filing:
    form: str
    filing_date: date


class EdgarFilingsProvider:
    """Point-in-time recent-filing flags from SEC EDGAR. Implements ``FilingsProvider``."""

    def __init__(
        self,
        *,
        user_agent: str,
        recency_days: int,
        cache_dir: str | Path,
        fetch: FetchFn = _default_fetch,
    ) -> None:
        if not user_agent:
            raise ValueError("EDGAR requires a non-empty user_agent (SEC returns 403 without it)")
        self._headers = {"User-Agent": user_agent}
        self._recency = timedelta(days=recency_days)
        self._dir = Path(cache_dir) / "edgar"
        self._fetch = fetch
        self._ticker_map: dict[str, str] | None = None
        # symbol (upper) -> (fetched_on, filings). ``None`` marks a known-unresolvable ticker.
        self._memo: dict[str, tuple[date, list[_Filing]] | None] = {}

    # -- FilingsProvider --------------------------------------------------------------
    def flags_as_of(self, symbols: Sequence[str], as_of: date) -> Mapping[str, FilingFlags]:
        """Per-symbol flags; a symbol is included only when at least one flag is set."""
        window_start = as_of - self._recency
        out: dict[str, FilingFlags] = {}
        for symbol in symbols:
            filings = self._filings_for(symbol, as_of)
            if not filings:
                continue
            recent_8k = any(
                f.form == _FORM_8K and window_start <= f.filing_date <= as_of for f in filings
            )
            insider = any(
                f.form == _FORM_4 and window_start <= f.filing_date <= as_of for f in filings
            )
            if recent_8k or insider:
                out[symbol] = FilingFlags(recent_8k=recent_8k, recent_insider_buy=insider)
        return out

    # -- internals --------------------------------------------------------------------
    def _filings_for(self, symbol: str, as_of: date) -> list[_Filing] | None:
        """Cached filing list for ``symbol``, fresh enough to answer ``as_of`` (else fetched)."""
        key = symbol.upper()
        if key in self._memo:
            entry = self._memo[key]
            if entry is None:
                return None
            fetched_on, filings = entry
            if as_of <= fetched_on:
                return filings
            # Cache predates this as_of (live, new day) -> fall through and re-fetch.
        else:
            disk = self._read_cache(key)
            if disk is not None and as_of <= disk[0]:
                self._memo[key] = disk
                return disk[1]

        cik = self._cik_for(key)
        if cik is None:
            self._memo[key] = None
            return None
        filings = self._fetch_filings(cik)
        fetched_on = date.today()
        self._memo[key] = (fetched_on, filings)
        self._write_cache(key, fetched_on, filings)
        return filings

    def _cik_for(self, symbol: str) -> str | None:
        if self._ticker_map is None:
            self._ticker_map = self._load_ticker_map()
        return self._ticker_map.get(symbol.upper())

    def _load_ticker_map(self) -> dict[str, str]:
        path = self._dir / "_tickers.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = self._fetch(_TICKERS_URL, self._headers)
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw), encoding="utf-8")
        out: dict[str, str] = {}
        for row in raw.values():
            ticker = str(row["ticker"]).upper()
            out[ticker] = str(row["cik_str"]).zfill(10)
        return out

    def _fetch_filings(self, cik: str) -> list[_Filing]:
        data = self._fetch(_SUBMISSIONS_URL.format(cik=cik), self._headers)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        filings: list[_Filing] = []
        for form, filed in zip(forms, dates, strict=False):
            if form in (_FORM_8K, _FORM_4):
                filings.append(_Filing(form=str(form), filing_date=date.fromisoformat(filed)))
        return filings

    def _read_cache(self, symbol: str) -> tuple[date, list[_Filing]] | None:
        path = self._dir / f"{symbol}.json"
        if not path.exists():
            return None
        rec = json.loads(path.read_text(encoding="utf-8"))
        fetched_on = date.fromisoformat(rec["fetched_on"])
        filings = [
            _Filing(form=f["form"], filing_date=date.fromisoformat(f["date"]))
            for f in rec["filings"]
        ]
        return fetched_on, filings

    def _write_cache(self, symbol: str, fetched_on: date, filings: list[_Filing]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{symbol}.json").write_text(
            json.dumps({
                "fetched_on": fetched_on.isoformat(),
                "filings": [
                    {"form": f.form, "date": f.filing_date.isoformat()} for f in filings
                ],
            }),
            encoding="utf-8",
        )


def build_edgar_filings_provider(
    settings: Settings, *, fetch: FetchFn = _default_fetch
) -> EdgarFilingsProvider:
    """Composition root: construct the provider from ``IngestConfig`` (real ``requests`` here)."""
    return EdgarFilingsProvider(
        user_agent=settings.ingest.edgar_user_agent,
        recency_days=settings.ingest.filings_recency_days,
        cache_dir=settings.ingest.edgar_cache_dir,
        fetch=fetch,
    )
