"""Ingest layer: assembles an immutable point-in-time ``MarketSnapshot``.

Public surface: the snapshot type, the assembler + its factory, the secondary-feed
interfaces, and the no-look-ahead guard. Secondary feeds are null stubs in Phase 1.
"""

from ingest.assembler import SnapshotAssembler
from ingest.factory import build_snapshot_assembler
from ingest.feeds import (
    FilingsProvider,
    MacroProvider,
    NewsProvider,
    NullFilings,
    NullMacro,
    NullNews,
)
from ingest.guard import LookAheadError, assert_no_look_ahead
from ingest.snapshot import MarketSnapshot

__all__ = [
    "MarketSnapshot",
    "SnapshotAssembler",
    "build_snapshot_assembler",
    "FilingsProvider",
    "NewsProvider",
    "MacroProvider",
    "NullFilings",
    "NullNews",
    "NullMacro",
    "LookAheadError",
    "assert_no_look_ahead",
]
