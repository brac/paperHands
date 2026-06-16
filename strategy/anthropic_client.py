"""Anthropic-backed :class:`LLMClient` for llm-mode strategy.

Wraps an injected ``anthropic.Anthropic``-like client so the rest of ``strategy`` depends only
on the ``LLMClient`` protocol — never on the SDK. The real ``anthropic`` import lives inside the
``build_anthropic_client`` factory (lazy), so this module imports, and unit tests run, with no
SDK installed and no network: tests inject a stub client. Exceptions are *not* caught here —
``llm_propose`` already wraps ``complete`` and falls back to a safe empty plan.
"""

from __future__ import annotations

from typing import Any

from core.config import Settings


class AnthropicClient:
    """:class:`LLMClient` implementation over an injected Anthropic-like client."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = "claude-opus-4-8",
        max_tokens: int = 1024,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        """Return the model's text completion for the given system + user prompt.

        Calls ``messages.create`` and concatenates the ``.text`` of every content block
        (the Messages API returns a list of blocks; non-text blocks are skipped).
        """
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        blocks = getattr(response, "content", None) or []
        return "".join(getattr(block, "text", "") for block in blocks)


def build_anthropic_client(settings: Settings) -> AnthropicClient:
    """Construct an :class:`AnthropicClient` from settings (lazy ``anthropic`` import).

    Raises ``RuntimeError`` if ``settings.anthropic_api_key`` is missing, so the caller fails
    fast with a clear message rather than at first request time.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; cannot build the Anthropic LLM client. "
            "Set it in .env or switch strategy_mode to rules-only."
        )
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return AnthropicClient(client)
