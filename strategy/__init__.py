"""The swappable strategy core: ``propose_plan`` + the LLM client contract.

Interface only in this slice. The dual-mode ``propose_plan`` (rules-only first, llm after)
arrives with the Strategy slice. The LLM client is injected so it can be stubbed in tests
and so the same pure ``propose_plan`` runs in backtest, paper, and live.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal contract for the injected LLM. Implementations call the real API; tests stub.

    Returns the model's raw text response; strategy code is responsible for parsing it
    into a validated plan (or falling back to a safe hold plan).
    """

    def complete(self, system: str, user: str) -> str:
        """Return the model's text completion for the given system + user prompt."""
        ...
