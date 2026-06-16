"""Structured, leveled logging setup.

A single ``configure_logging`` call (idempotent) at the composition root, and
``get_logger`` everywhere else. Kept on the stdlib to avoid a dependency; the format is
key=value-ish so records stay grep-friendly and easy to upgrade to JSON later.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once. Safe to call repeatedly (only the first call wins)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (configure_logging should have run at startup)."""
    return logging.getLogger(name)
