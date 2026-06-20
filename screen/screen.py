"""The pure screen — reduce a broad universe to a ranked candidate set under user knobs.

This is the user's sole control point: downstream stages reason only over what survives here.
The function is pure (no I/O, no clock) and deterministic — identical input yields identical
output, including the symbol-ascending tie-break.

It reads only ``snapshot.prices`` (canonical bar frames) and ``snapshot.news`` by duck typing,
so ``MarketSnapshot`` is imported under ``TYPE_CHECKING`` only — keeping ``screen`` free of any
runtime dependency on ``ingest`` (and the ``ingest -> data -> core.config`` chain).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from core.config import ScreenConfig
from core.contracts import SymbolMetadata
from screen.result import Candidate, ScreenResult

if TYPE_CHECKING:
    from ingest.snapshot import MarketSnapshot


def screen(
    snapshot: MarketSnapshot,
    metadata: Mapping[str, SymbolMetadata],
    config: ScreenConfig,
) -> ScreenResult:
    """Filter and rank ``snapshot.prices`` into a ≤ ``max_candidates`` candidate set.

    Steps (per symbol): drop on insufficient history, then the hard tradeability floors
    (liquidity, min-price) which apply to *every* symbol; then the sector filter (skipped for
    watchlist symbols); then score = momentum_weight*ROC + relevance_weight*sentiment.
    Watchlist survivors take priority slots, the rest fill by score desc (symbol-asc
    tie-break), truncated to ``max_candidates``.
    """
    watchlist = set(config.watchlist)
    min_bars = max(config.liquidity_window, config.momentum_window) + 1

    dropped: list[tuple[str, str]] = []
    scored: list[tuple[str, float, str]] = []  # (symbol, score, sector)

    for symbol, df in snapshot.prices.items():
        if len(df) < min_bars:
            dropped.append((symbol, "insufficient history"))
            continue

        window = config.liquidity_window
        dollar_volume = df["close"].iloc[-window:] * df["volume"].iloc[-window:]
        adv = float(dollar_volume.mean())
        if adv < config.min_avg_dollar_volume:
            dropped.append((symbol, "below liquidity floor"))
            continue

        latest_close = float(df["close"].iloc[-1])
        if latest_close < config.min_price:
            dropped.append((symbol, "below min price floor"))
            continue

        meta = metadata.get(symbol)
        sector = meta.sector if meta is not None else ""
        is_pinned = symbol in watchlist
        if not is_pinned:
            if config.sectors_include and sector not in config.sectors_include:
                dropped.append((symbol, "sector not included"))
                continue
            if sector in config.sectors_exclude:
                dropped.append((symbol, "sector excluded"))
                continue

        adj_close = df["adj_close"]
        prior = float(adj_close.iloc[-1 - config.momentum_window])
        momentum = float(adj_close.iloc[-1]) / prior - 1.0 if prior else 0.0
        news = snapshot.news.get(symbol)
        relevance = (news.sentiment or 0.0) if news is not None else 0.0
        score = config.momentum_weight * momentum + config.relevance_weight * relevance

        scored.append((symbol, score, sector))

    # Order: pinned watchlist survivors first (priority slots), then the rest by score desc.
    # Within each group, tie-break by symbol ascending for determinism.
    pinned = sorted((s for s in scored if s[0] in watchlist), key=lambda r: r[0])
    rest = sorted((s for s in scored if s[0] not in watchlist), key=lambda r: (-r[1], r[0]))
    ordered = (pinned + rest)[: config.max_candidates]

    candidates = tuple(
        Candidate(symbol=sym, score=sc, rank=i, sector=sect)
        for i, (sym, sc, sect) in enumerate(ordered, start=1)
    )
    return ScreenResult(candidates=candidates, dropped=tuple(dropped))
