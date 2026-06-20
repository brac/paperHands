"""Technical signal computation: pure indicators + a per-candidate SignalSet.

Public surface: ``compute_signals`` and the ``SignalSet`` contract. Indicators are pure
functions over canonical bar frames; the SignalSet also carries the secondary news/filing
flags attached from the snapshot (technicals stay primary).
"""

from signals.signals import compute_signals
from signals.signalset import SignalSet

__all__ = ["compute_signals", "SignalSet"]
