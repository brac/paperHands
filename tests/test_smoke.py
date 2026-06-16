"""The smoke entrypoint runs clean and exits 0 with no secrets configured."""

from __future__ import annotations

from runner.smoke import main


def test_smoke_exits_zero(monkeypatch):
    # Ensure no .env secrets leak into the test; rely on defaults.
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main() == 0
