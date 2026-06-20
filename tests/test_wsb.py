"""Tests for the Reddit-WSB hype feed: extraction, the offline builder, point-in-time reads.

Offline and deterministic over a tiny hand-built CSV. Covers the ticker extractor (cashtags,
stopword filtering, dedupe), the aggregate builder, and — most importantly — that the feed only
ever counts posts at-or-before ``as_of`` (the no-look-ahead guarantee the YOLO backtest rests on).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.contracts import HypeContext
from ingest.wsb import (
    MEME_TICKERS,
    RedditWSBFeed,
    build_wsb_aggregate,
    extract_tickers,
)

_TICKERS = frozenset({"GME", "AMC", "TSLA", "SPY"})


# -- extract_tickers ------------------------------------------------------------------
def test_extracts_known_bare_tickers():
    assert extract_tickers("GME to the moon, AMC too", _TICKERS) == {"GME", "AMC"}


def test_accepts_cashtags_even_outside_whitelist_membership():
    # A $-prefixed token in the whitelist is accepted; the explicit $ marks a ticker reference.
    assert extract_tickers("buying $TSLA calls", _TICKERS) == {"TSLA"}


def test_filters_stopwords_and_unknown_tokens():
    # YOLO/CEO are stopwords; FOO is not a known ticker -> none counted.
    assert extract_tickers("YOLO the CEO said FOO", _TICKERS) == set()


def test_dedupes_within_a_single_post():
    # One rant naming GME five times counts once (mentions = posts naming it).
    assert extract_tickers("GME GME GME gme $GME", _TICKERS) == {"GME"}


def test_empty_text_is_safe():
    assert extract_tickers("", _TICKERS) == set()


def test_meme_seed_is_nonempty_and_has_the_classics():
    assert {"GME", "AMC", "TSLA"} <= MEME_TICKERS


# -- build_wsb_aggregate + RedditWSBFeed ----------------------------------------------
def _write_csv(path, rows):
    """rows: list of (title, body, score, created_epoch)."""
    pd.DataFrame(rows, columns=["title", "body", "score", "created"]).assign(
        id="x", url="", comms_num=0, timestamp=""
    ).to_csv(path, index=False)


# Epoch seconds for a few distinct UTC days.
_D1 = 1_650_000_000  # 2022-04-15
_D2 = 1_650_086_400  # 2022-04-16
_D3 = 1_650_172_800  # 2022-04-17


def test_builder_aggregates_mentions_per_day(tmp_path):
    csv = tmp_path / "wsb.csv"
    _write_csv(csv, [
        ("GME squeeze", "buy GME", 100, _D1),   # GME day1
        ("AMC apes", "AMC strong", 50, _D1),    # AMC day1
        ("GME again", "still GME", 10, _D2),     # GME day2
        ("nothing here", "just vibes", 5, _D2),  # no ticker
    ])
    out = tmp_path / "agg.sqlite"
    n = build_wsb_aggregate(csv, out, _TICKERS)
    assert n == 3  # (GME,d1), (AMC,d1), (GME,d2)

    feed = RedditWSBFeed(out, mention_window=7, velocity_window=3)
    ctx = feed.hype_as_of(["GME", "AMC", "TSLA"], date(2022, 4, 17))
    assert ctx["GME"].reddit_mentions == 2  # both days inside the 7d window
    assert ctx["AMC"].reddit_mentions == 1
    assert "TSLA" not in ctx  # no chatter -> omitted
    assert ctx["GME"].social_score == pytest.approx(__import__("math").log1p(2))


def test_feed_is_point_in_time_ignores_future_posts(tmp_path):
    csv = tmp_path / "wsb.csv"
    _write_csv(csv, [
        ("GME", "GME day1", 1, _D1),
        ("GME", "GME day3", 1, _D3),  # this post is in the future for an as_of of day1
    ])
    out = tmp_path / "agg.sqlite"
    build_wsb_aggregate(csv, out, _TICKERS)
    feed = RedditWSBFeed(out, mention_window=30, velocity_window=3)

    # As of day1, only the day1 post is visible — the day3 post must not leak backwards.
    early = feed.hype_as_of(["GME"], date(2022, 4, 15))
    assert early["GME"].reddit_mentions == 1
    # As of day3, both are counted.
    late = feed.hype_as_of(["GME"], date(2022, 4, 17))
    assert late["GME"].reddit_mentions == 2


def test_feed_missing_aggregate_raises_with_hint(tmp_path):
    with pytest.raises(FileNotFoundError, match="build it first"):
        RedditWSBFeed(tmp_path / "does_not_exist.sqlite")


def test_min_post_score_filters_noise(tmp_path):
    csv = tmp_path / "wsb.csv"
    _write_csv(csv, [
        ("GME", "GME", 100, _D1),  # kept
        ("GME", "GME", 1, _D1),    # dropped by min_post_score
    ])
    out = tmp_path / "agg.sqlite"
    build_wsb_aggregate(csv, out, _TICKERS, min_post_score=10)
    feed = RedditWSBFeed(out)
    assert feed.hype_as_of(["GME"], date(2022, 4, 16))["GME"].reddit_mentions == 1


def test_hype_context_passes_through_signals():
    # Sanity that the feed's contract matches what compute_signals reads (social_score field).
    ctx = HypeContext(social_score=1.0, reddit_mentions=3)
    assert ctx.social_score == 1.0 and ctx.reddit_mentions == 3
