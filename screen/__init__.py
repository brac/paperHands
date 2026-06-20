"""User-tunable universe filter/ranking (config-driven). Pure function over a snapshot.

The ``screen`` function reduces a broad liquid universe (sourced via a ``UniverseProvider``)
to a deterministic, ranked candidate set under the user's ``ScreenConfig`` knobs. It is the
user's sole control point — downstream stages reason only over what survives.
"""

from __future__ import annotations

from screen.result import Candidate, ScreenResult
from screen.screen import screen
from screen.universe import (
    StaticUniverseProvider,
    UniverseProvider,
    build_universe_provider,
)

__all__ = [
    "Candidate",
    "ScreenResult",
    "StaticUniverseProvider",
    "UniverseProvider",
    "build_universe_provider",
    "screen",
]
