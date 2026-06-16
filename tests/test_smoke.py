"""The smoke entrypoint runs clean and exits 0 with no secrets configured.

Must stay hermetic: force the no-key skip path so ``main()`` never touches the network,
even when a real ``.env`` with a Tiingo key is present on the developer's machine. An empty
env var overrides the ``.env`` file value in pydantic-settings.
"""

from __future__ import annotations

from runner.smoke import main


def test_smoke_exits_zero(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert main() == 0
