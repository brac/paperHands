# PaperHands

A self-benchmarking algorithmic trading experiment. See `docs/PROJECT.md` for the project
bible and `docs/PHASE1_SPEC_backtest.md` for the active phase (the backtest harness).

> **North star:** answer *"does this strategy have edge?"* on history before risking a
> cent. Process edge over information edge. The risk gate is sovereign — the strategy
> proposes, the deterministic gate disposes.

## Status

First slice: **scaffold + the sovereign risk gate** (`risk/`), fully tested. The data
provider, ingest, screen, signals, strategy, simulated broker, engine, and record/benchmark
modules are stubbed package skeletons, built in subsequent slices.

## Quickstart

This project uses [uv](https://docs.astral.sh/uv/) (cross-platform, Windows + macOS).

```bash
# Install uv (if needed):
#   macOS:   curl -LsSf https://astral.sh/uv/install.sh | sh
#   Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv sync --extra dev          # create venv + install deps
uv run python -m runner.smoke   # loads config + logging, exits 0
uv run pytest                # risk-gate adversarial + property tests, smoke
```

### Fallback (no uv)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m runner.smoke
pytest
```

## Configuration

Copy `.env.example` to `.env` and adjust. Secrets are loaded from env only and never
committed. Risk-gate knobs and strategy mode are config-driven.

## Layout

Top-level packages mirror the module boundaries in `docs/PROJECT.md`:
`core/ data/ ingest/ screen/ signals/ strategy/ risk/ broker/ engine/ record/ runner/`,
plus `tests/`.
