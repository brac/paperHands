"""The sovereign risk gate.

The strategy proposes; this deterministic, pure gate disposes. It is the single module
that must *provably* never let an unsafe plan through, and it is reused unchanged across
backtest, paper, and live. No I/O, no LLM, no surprises.
"""

from risk.gate import apply_risk_gate
from risk.params import RiskParams

__all__ = ["apply_risk_gate", "RiskParams"]
