"""The per-candidate SignalSet — the structured numeric input the strategy reasons over.

Frozen, fully JSON-serializable (every field is a primitive or None), because in ``llm`` mode
it is serialized straight into the prompt. Technical indicators and the secondary news/filing
flags are kept as *separate fields* so the strategy can weight them per the technicals-primary
doctrine — signals computes and attaches; it does not decide.

Lives in the ``signals`` package (mirroring ``screen/result.py``), not ``core/contracts.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SignalSet:
    """Indicators + secondary flags for one candidate. Optional floats are ``None`` when a
    window does not fit the available history (never NaN)."""

    symbol: str
    price: float | None = None  # latest raw close (human-facing / tradeable price)

    # Technicals (primary).
    sma_short: float | None = None
    sma_long: float | None = None
    trend_strength: float | None = None  # sma_short / sma_long - 1
    roc: float | None = None
    rsi: float | None = None
    atr_pct: float | None = None  # ATR / latest close
    zscore: float | None = None
    dist_from_high: float | None = None  # adj_close / rolling-high - 1 (in (-1, 0]; ~0 = at high)

    # Secondary flags (attached from the snapshot; never originate a trade).
    recent_8k: bool = False
    recent_insider_buy: bool = False
    news_sentiment: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view (for the record layer and the LLM prompt)."""
        return asdict(self)
