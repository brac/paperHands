"""The injected LLM client contract.

In its own leaf module (imported by ``llm``/``context`` and re-exported from the package)
so it carries no dependency on the rest of ``strategy`` — no import cycles.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal contract for the injected LLM. Implementations call the real API; tests stub.

    Returns the model's raw text response; strategy code is responsible for parsing it into a
    validated plan (or falling back to a safe empty plan).
    """

    def complete(self, system: str, user: str) -> str:
        """Return the model's text completion for the given system + user prompt."""
        ...
