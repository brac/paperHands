"""Reddit r/wallstreetbets hype feed — the YOLO sleeve's real 'meme mindset' input.

Turns a raw WSB post/comment archive (``title,score,id,url,comms_num,created,body,timestamp``,
~millions of rows) into a per-symbol, point-in-time **hype** signal: how loudly the crowd is
talking about a ticker, right now, using only posts that existed at-or-before the decision date.

Two pieces, split so the expensive parse runs once:

1. ``build_wsb_aggregate`` (offline, ``python -m ingest.wsb``): streams the CSV in chunks,
   extracts ticker mentions from each post's text, and writes a compact, indexed SQLite table
   ``wsb_daily(date, symbol, mentions, score_sum, posts)``. This is the only place that touches
   the giant file.
2. ``RedditWSBFeed`` (a ``SocialProvider``): at each decision it reads the aggregate for a
   trailing window ending at ``as_of`` and emits a ``HypeContext`` per symbol — mention count,
   mention velocity (recent vs prior window), and a compressed ``social_score`` the YOLO blend
   consumes via ``YoloConfig.social_weight``.

**Meme mindset, made concrete.** The signal is deliberately attention-driven, not value-driven:
a name lighting up WSB — mention volume accelerating — *is* the buy thesis for this sleeve. It is
crowd momentum, the same impulse that drove GME/AMC, encoded as a number. It is also paper-only
and honest about its nature: high mention count is hype, not edge, and the no-look-ahead rule is
enforced by only ever summing days ``<= as_of``.

**Point-in-time integrity.** Each post's day comes from its ``created`` epoch (when it became
visible). The feed filters ``date <= as_of`` in SQL, so a backtest decision never sees a post
from its future — provided the archive stores original post times (an edited/deleted-backfilled
scrape would violate this; that is the data's responsibility, flagged in PROJECT.md).
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from core.contracts import HypeContext

# A curated meme/large-cap seed so the extractor has a sensible default ticker set even without a
# universe provider. Not exhaustive — the builder unions this with whatever tickers it is given.
MEME_TICKERS: frozenset[str] = frozenset({
    "GME", "AMC", "TSLA", "NVDA", "AMD", "AAPL", "MSFT", "META", "AMZN", "GOOG", "GOOGL",
    "NFLX", "PLTR", "COIN", "MSTR", "HOOD", "SOFI", "NIO", "BABA", "BB", "NOK", "SPCE",
    "DKNG", "RIVN", "LCID", "F", "GM", "INTC", "MU", "SMCI", "ARM", "DJT", "RDDT", "BBBY",
    "WISH", "CLOV", "TLRY", "SNDL", "MARA", "RIOT", "SQ", "PYPL", "SHOP", "ROKU", "ZM",
    "SPY", "QQQ", "TQQQ", "SQQQ", "SOXL", "VXX", "UVXY", "IWM", "GLD", "SLV", "BAC", "T",
})

# All-caps tokens that look like tickers but are WSB slang / common words. Excluded from the bare-
# token match so the mention counts stay about *companies*, not chatter. (Cashtags like ``$GME``
# bypass this list — an explicit ``$`` is an unambiguous ticker reference.)
MEME_STOPWORDS: frozenset[str] = frozenset({
    "YOLO", "DD", "FD", "FDS", "CEO", "CFO", "IPO", "ETF", "WSB", "OTM", "ITM", "ATH", "ATL",
    "EOD", "EOW", "EOY", "AH", "PM", "AM", "PT", "TA", "RH", "IRA", "USA", "US", "UK", "EU",
    "IMO", "IMHO", "TLDR", "FOMO", "HODL", "BTFD", "MOON", "APE", "APES", "GG", "EV",
    "ER", "EPS", "PE", "GDP", "CPI", "FED", "FOMC", "SEC", "IRS", "LOL", "LMAO", "WTF", "OMG",
    "AF", "ASAP", "FYI", "OK", "NO", "YES", "ANY", "FOR", "AND", "THE", "NOT", "BUT",
    "YOU", "ARE", "WAS", "CAN", "GET", "GOT", "BUY", "PUT", "CALL", "PUTS", "SELL", "HOLD",
    "BIG", "RED", "NEW", "WAY", "WIN", "TOP", "LOW", "HIGH", "OUT", "OFF", "NOW", "DAY", "ONE",
    "TWO", "TEN", "BRO", "GUY", "MAN", "GAY", "ROPE", "LOSS", "GAIN", "RISK", "BEAR", "BULL",
    "PUMP", "DUMP", "BAG", "BAGS", "TENDIES", "RETARD", "AUTIST", "STONK", "STONKS", "CASH",
})

# Cashtags ($GME) are always tickers; bare ALLCAPS tokens (2-5 letters) are candidates filtered
# against the known-ticker set minus the stoplist.
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_BARE = re.compile(r"\b([A-Z]{2,5})\b")


def extract_tickers(
    text: str, tickers: frozenset[str], stopwords: frozenset[str] = MEME_STOPWORDS
) -> set[str]:
    """Return the set of distinct tickers a single post mentions (deduped within the post).

    Pure and deterministic. A ``$AAA`` cashtag is always accepted (an explicit ticker reference);
    a bare ``AAA`` is accepted only if it is in ``tickers`` and not in ``stopwords``. Deduping
    within a post means one rant about GME counts once, so ``mentions`` measures *how many posts*
    name a ticker, not how many times — a steadier crowd-attention measure.
    """
    if not text:
        return set()
    found: set[str] = set()
    for m in _CASHTAG.finditer(text):
        sym = m.group(1).upper()
        if sym in tickers:
            found.add(sym)
    for m in _BARE.finditer(text):
        sym = m.group(1)
        if sym in tickers and sym not in stopwords:
            found.add(sym)
    return found


_SCHEMA = """
CREATE TABLE IF NOT EXISTS wsb_daily (
    date TEXT, symbol TEXT, mentions INTEGER, score_sum REAL, posts INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS ix_wsb_symbol_date ON wsb_daily (symbol, date);
"""


def build_wsb_aggregate(
    csv_path: str | Path,
    out_path: str | Path,
    tickers: frozenset[str],
    *,
    chunksize: int = 250_000,
    min_post_score: int = 0,
    stopwords: frozenset[str] = MEME_STOPWORDS,
) -> int:
    """Stream the raw WSB CSV → a compact ``wsb_daily`` SQLite aggregate. Returns rows written.

    Reads only the columns it needs in ``chunksize`` batches (the file is large), extracts ticker
    mentions per post, and accumulates per ``(date, symbol)``: ``mentions`` (posts naming it),
    ``score_sum`` (sum of those posts' reddit scores — a crowd-conviction proxy), and ``posts``.
    ``date`` is the post's ``created`` epoch in UTC (its point-in-time visibility day).
    """
    frames: list[pd.DataFrame] = []
    reader = pd.read_csv(
        csv_path,
        usecols=["title", "body", "score", "created"],
        chunksize=chunksize,
        dtype={"title": "string", "body": "string", "score": "string"},
        on_bad_lines="skip",
    )
    for chunk in reader:
        frames.append(_aggregate_chunk(chunk, tickers, min_post_score, stopwords))

    if not frames:
        agg = pd.DataFrame(columns=["date", "symbol", "mentions", "score_sum", "posts"])
    else:
        agg = (
            pd.concat(frames, ignore_index=True)
            .groupby(["date", "symbol"], as_index=False)
            .sum()
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(out_path)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("DELETE FROM wsb_daily")
        conn.executemany(
            "INSERT OR REPLACE INTO wsb_daily VALUES (?,?,?,?,?)",
            [
                (d, sym, int(mentions), float(score_sum), int(posts))
                for d, sym, mentions, score_sum, posts in agg.itertuples(
                    index=False, name=None)
            ],
        )
    return len(agg)


def _aggregate_chunk(
    chunk: pd.DataFrame,
    tickers: frozenset[str],
    min_post_score: int,
    stopwords: frozenset[str],
) -> pd.DataFrame:
    """Extract mentions from one CSV chunk → a partial ``(date, symbol)`` aggregate frame."""
    score = pd.to_numeric(chunk["score"], errors="coerce").fillna(0.0)
    if min_post_score > 0:
        keep = score >= min_post_score
        chunk, score = chunk[keep], score[keep]
    if chunk.empty:
        return pd.DataFrame(columns=["date", "symbol", "mentions", "score_sum", "posts"])

    created = pd.to_numeric(chunk["created"], errors="coerce")
    day = pd.to_datetime(created, unit="s", utc=True).dt.date
    text = chunk["title"].fillna("") + " " + chunk["body"].fillna("")

    rows: list[tuple[object, str, float]] = []
    for d, t, sc in zip(day, text.tolist(), score.tolist(), strict=True):
        if d is None or (isinstance(d, float) and math.isnan(d)):
            continue
        for sym in extract_tickers(t, tickers, stopwords):
            rows.append((d.isoformat(), sym, float(sc)))
    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "mentions", "score_sum", "posts"])

    df = pd.DataFrame(rows, columns=["date", "symbol", "score_sum"])
    return df.groupby(["date", "symbol"], as_index=False).agg(
        mentions=("score_sum", "size"),
        score_sum=("score_sum", "sum"),
        posts=("score_sum", "size"),
    )[["date", "symbol", "mentions", "score_sum", "posts"]]


class RedditWSBFeed:
    """A ``SocialProvider`` over the precomputed ``wsb_daily`` aggregate (point-in-time).

    For each decision it sums the trailing ``mention_window`` days (``<= as_of``) per symbol into
    a ``HypeContext``: ``reddit_mentions`` (raw count), ``mention_velocity`` (recent vs prior
    ``velocity_window``), and ``social_score`` — ``log1p`` of the mention count so the magnitude
    sits on a scale comparable to the price/volume proxy components (mentions can run to the
    hundreds; the YOLO blend would otherwise be swamped). Names with no chatter return nothing,
    so the sleeve simply ignores them.
    """

    def __init__(
        self, aggregate_path: str | Path, *, mention_window: int = 7, velocity_window: int = 3
    ) -> None:
        self._path = str(aggregate_path)
        if not Path(self._path).exists():
            raise FileNotFoundError(
                f"WSB aggregate not found at {self._path!r}; build it first with "
                "`python -m ingest.wsb --csv <archive.csv> --out <aggregate.sqlite>`"
            )
        self._mention_window = mention_window
        self._velocity_window = velocity_window
        self._conn = sqlite3.connect(self._path)

    def hype_as_of(
        self, symbols: Sequence[str], as_of: date
    ) -> Mapping[str, HypeContext]:
        """Per-symbol WSB hype using only days at-or-before ``as_of`` (no look-ahead)."""
        if not symbols:
            return {}
        lookback = max(self._mention_window, 2 * self._velocity_window)
        start = as_of - timedelta(days=lookback)
        per_day = self._mentions_by_day(symbols, start, as_of)

        out: dict[str, HypeContext] = {}
        for symbol in symbols:
            days = per_day.get(symbol)
            if not days:
                continue
            out[symbol] = self._context(days, as_of)
        return out

    def _mentions_by_day(
        self, symbols: Sequence[str], start: date, as_of: date
    ) -> dict[str, dict[date, int]]:
        placeholders = ",".join("?" for _ in symbols)
        rows = self._conn.execute(
            f"SELECT symbol, date, mentions FROM wsb_daily "  # noqa: S608 - placeholders are bound
            f"WHERE symbol IN ({placeholders}) AND date > ? AND date <= ? ",
            (*symbols, start.isoformat(), as_of.isoformat()),
        ).fetchall()
        by_symbol: dict[str, dict[date, int]] = {}
        for symbol, day, mentions in rows:
            by_symbol.setdefault(symbol, {})[date.fromisoformat(day)] = int(mentions)
        return by_symbol

    def _context(self, days: Mapping[date, int], as_of: date) -> HypeContext:
        window_start = as_of - timedelta(days=self._mention_window)
        mentions = sum(m for d, m in days.items() if d > window_start)

        v = self._velocity_window
        recent = sum(m for d, m in days.items() if d > as_of - timedelta(days=v))
        prior = sum(
            m for d, m in days.items()
            if as_of - timedelta(days=2 * v) < d <= as_of - timedelta(days=v)
        )
        velocity = (recent / prior - 1.0) if prior > 0 else (float(recent) if recent else 0.0)

        return HypeContext(
            social_score=math.log1p(mentions) if mentions > 0 else None,
            mention_velocity=velocity,
            reddit_mentions=mentions,
        )


def _default_tickers() -> frozenset[str]:
    """Builder default ticker set: the configured universe ∪ the curated meme seed."""
    try:
        from core.config import load_settings
        from screen import build_universe_provider

        universe = frozenset(build_universe_provider(load_settings()).symbols())
    except Exception:  # noqa: BLE001 - the offline builder must work without data creds
        universe = frozenset()
    return universe | MEME_TICKERS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest.wsb",
        description="Build the point-in-time WSB hype aggregate from a raw post archive.",
    )
    parser.add_argument("--csv", help="raw WSB archive CSV (default: from PAPERHANDS_SOCIAL__)")
    parser.add_argument("--out", help="output SQLite aggregate (default: from PAPERHANDS_SOCIAL__)")
    parser.add_argument(
        "--tickers", help="comma-separated ticker whitelist (default: universe ∪ meme seed)")
    parser.add_argument("--min-score", type=int, default=None, help="drop posts below this score")
    args = parser.parse_args(argv)

    from core.config import load_settings

    settings = load_settings()
    csv_path = args.csv or settings.social.wsb_csv_path
    out_path = args.out or settings.social.aggregate_path
    if not csv_path:
        parser.error("no CSV given (--csv or PAPERHANDS_SOCIAL__WSB_CSV_PATH)")

    tickers = (
        frozenset(_parse_csv_list(args.tickers)) if args.tickers else _default_tickers()
    )
    min_score = args.min_score if args.min_score is not None else settings.social.min_post_score

    n = build_wsb_aggregate(csv_path, out_path, tickers, min_post_score=min_score)
    print(f"wrote {n} (symbol, date) rows to {out_path} from {csv_path} "
          f"({len(tickers)} tickers in whitelist)")
    return 0


def _parse_csv_list(value: str) -> Iterable[str]:
    return (s.strip().upper() for s in value.split(",") if s.strip())


if __name__ == "__main__":
    sys.exit(main())
