"""Composition root for the snapshot assembler.

Wires the (Phase-1 null) secondary feeds and the configured history window onto an
already-constructed ``DataProvider``. Lives here, not in a pure module, because the
assembler does I/O via its dependencies.
"""

from __future__ import annotations

from core.config import Settings
from data.base import DataProvider
from ingest.assembler import SnapshotAssembler


def build_snapshot_assembler(
    settings: Settings, data_provider: DataProvider
) -> SnapshotAssembler:
    """Build the assembler from config + an injected data provider.

    Secondary feeds default to the null stubs (Phase 1); the real EDGAR/FRED/news clients
    will be passed here when that slice lands.
    """
    return SnapshotAssembler(
        data_provider,
        history_days=settings.ingest.history_days,
    )
