"""Tests for the Anthropic LLM client — fully offline (the SDK client is injected/stubbed).

No network, no installed ``anthropic``: a stub whose ``messages.create`` returns a canned
response stands in for the SDK, mirroring the dependency-injection discipline used elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from strategy import AnthropicClient, build_anthropic_client
from strategy.client import LLMClient


@dataclass
class _Block:
    """A canned content block exposing ``.text`` like the SDK's TextBlock."""

    text: str


@dataclass
class _Response:
    content: list[Any]


class _StubMessages:
    """Records the kwargs of the last ``create`` call and returns canned blocks."""

    def __init__(self, blocks: list[Any]) -> None:
        self._blocks = blocks
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _Response:
        self.last_kwargs = kwargs
        return _Response(content=self._blocks)


class _StubClient:
    def __init__(self, blocks: list[Any]) -> None:
        self.messages = _StubMessages(blocks)


def test_implements_protocol() -> None:
    client = AnthropicClient(_StubClient([_Block("[]")]))
    assert isinstance(client, LLMClient)


def test_complete_returns_block_text() -> None:
    client = AnthropicClient(_StubClient([_Block("[]")]))
    assert client.complete("sys", "usr") == "[]"


def test_complete_concatenates_multiple_blocks() -> None:
    blocks = [_Block('[{"a": '), _Block("1"), _Block("}]")]
    client = AnthropicClient(_StubClient(blocks))
    assert client.complete("sys", "usr") == '[{"a": 1}]'


def test_complete_passes_system_user_and_defaults() -> None:
    stub = _StubClient([_Block("ok")])
    client = AnthropicClient(stub)

    client.complete("the-system", "the-user")

    kwargs = stub.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["system"] == "the-system"
    assert kwargs["messages"] == [{"role": "user", "content": "the-user"}]
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 1024


def test_complete_honors_overrides() -> None:
    stub = _StubClient([_Block("ok")])
    client = AnthropicClient(stub, model="claude-haiku", max_tokens=64)

    client.complete("s", "u")

    kwargs = stub.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-haiku"
    assert kwargs["max_tokens"] == 64


def test_complete_skips_blocks_without_text() -> None:
    class _Other:
        pass

    client = AnthropicClient(_StubClient([_Block("a"), _Other(), _Block("b")]))
    assert client.complete("s", "u") == "ab"


def test_complete_propagates_exceptions() -> None:
    class _Boom:
        class messages:  # noqa: N801 - mimic SDK attribute shape
            @staticmethod
            def create(**kwargs: Any) -> Any:
                raise RuntimeError("api down")

    with pytest.raises(RuntimeError, match="api down"):
        AnthropicClient(_Boom()).complete("s", "u")


def test_build_requires_api_key() -> None:
    @dataclass
    class _Settings:
        anthropic_api_key: str | None = None

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        build_anthropic_client(_Settings())  # type: ignore[arg-type]
