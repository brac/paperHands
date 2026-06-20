"""Composition root for the snapshot assembler.

Wires the (Phase-1 null) secondary feeds and the configured history window onto an
already-constructed ``DataProvider``. Lives here, not in a pure module, because the
assembler does I/O via its dependencies.
"""

from __future__ import annotations

from core.config import Settings
from data.base import DataProvider
from ingest.assembler import SnapshotAssembler
from ingest.feeds import FilingsProvider, SocialProvider


def build_snapshot_assembler(
    settings: Settings, data_provider: DataProvider
) -> SnapshotAssembler:
    """Build the assembler from config + an injected data provider.

    The real SEC-EDGAR filings feed is wired in when ``ingest.filings_enabled`` is set; the
    Reddit-WSB hype feed when ``social.enabled`` is set (the YOLO sleeve's real meme input).
    Otherwise filings/news/macro/social default to the null stubs. FRED/news remain deferred.
    """
    filings: FilingsProvider | None = None
    if settings.ingest.filings_enabled:
        from ingest.edgar import build_edgar_filings_provider

        filings = build_edgar_filings_provider(settings)

    social: SocialProvider | None = None
    if settings.social.enabled and settings.social.source == "wsb":
        from ingest.wsb import RedditWSBFeed

        social = RedditWSBFeed(
            settings.social.aggregate_path,
            mention_window=settings.social.mention_window,
            velocity_window=settings.social.velocity_window,
        )

    return SnapshotAssembler(
        data_provider,
        filings=filings,
        social=social,
        history_days=settings.ingest.history_days,
    )
