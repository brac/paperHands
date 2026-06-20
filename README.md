# PaperHands

A self-benchmarking, honest trading system. See `docs/PROJECT.md` for the project bible.

> **Goal (current):** match SPY with *less* risk via a hands-off ETF rebalancer — not to
> beat it. The risk gate is sovereign — the strategy proposes, the deterministic gate
> sizes and disposes. Every config change is validated through the backtest harness across
> regimes before it touches live money.

## Status

We built the survivorship-aware backtest harness and used it to test broad-screen technical
momentum / mean-reversion. The honest result: **no retail-reachable edge** (≈0 large-cap,
negative small-cap under realistic costs). So the project **pivoted** to a low-turnover ETF
rebalancer (`strategy/rebalance.py`); the disproven alpha logic is preserved, kept runnable,
under `legacy/` as the honest record. A read-only check-in dashboard lives in `dashboard/`.

## Quickstart

This project uses [uv](https://docs.astral.sh/uv/) (cross-platform, Windows + macOS).

```bash
# Install uv (if needed):
#   macOS:   curl -LsSf https://astral.sh/uv/install.sh | sh
#   Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv sync --extra dev          # create venv + install deps
uv run python -m runner.smoke   # loads config + logging, exits 0
uv run pytest                # risk-gate adversarial + property tests, rebalancer, smoke
```

### Run the rebalancer backtest + check-in dashboard

```bash
# Multi-window evaluation of a rebalance config vs SPY (bull / drawdown / chop):
uv run python -m runner.evaluate --mode rebalance

# A single window, then the text report:
uv run python -m runner.backtest --start 2018-01-01 --end 2023-12-31 --mode rebalance
uv run python -m record.report --latest

# Export the latest run to JSON and view the read-only dashboard:
uv run python -m dashboard.export --latest      # -> dashboard/web/public/data.json
cd dashboard/web && npm install && npm run dev
```

The rebalancer needs `PAPERHANDS_STRATEGY_MODE=rebalance`,
`PAPERHANDS_RISK__SIZING=target-weight`, and `PAPERHANDS_ENGINE__SCREEN_BYPASS=true`
(plus a `PAPERHANDS_REBALANCE__*` basket) — see `.env.example`. Or pass `--mode rebalance`,
which wires the universe and screen-bypass for you.

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
