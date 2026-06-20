# legacy/ — the honest record of what we disproved

This directory holds the original **alpha-hunting** strategy: a rules-based momentum +
mean-reversion proposer (`strategy/rules.py`) and an LLM-mode variant (`strategy/llm.py`),
with the technicals-primary doctrine guard (`strategy/guard.py`) that enforced
"news may modulate but never originate a trade."

## Why it's here and not deleted

We built this to answer one honest question: *does broad-screen technical momentum /
mean-reversion have a retail-reachable edge?* We ran it through the same survivorship-aware
backtest harness that gates everything else, across multiple regimes (bull / drawdown /
chop) and against a SPY benchmark under realistic costs:

- **Large-cap universe:** ≈ 0 edge.
- **Small-cap universe:** negative under honest (liquidity-aware) costs.

We accept that result. That strategy class does not have an edge we can reach. The backtest
gate did exactly its job — it stopped us from putting disproven alpha in front of real money.

So the project **pivoted** to a hands-off, low-turnover ETF rebalancer
(`strategy/rebalance.py`) whose honest goal is to roughly match SPY with shallower
drawdowns — not to beat it.

## Status

- **Kept runnable, not just archived.** `strategy/strategy.py` still dispatches to these
  modules for `strategy_mode = "rules-only" | "llm"`, and their tests still run, so the
  disproven result stays reproducible. Nothing here executes in the default (`rebalance`)
  core loop.
- These modules are evidence, not active code. Don't build on them; if you want to re-run
  the comparison that justified the pivot, point `PAPERHANDS_STRATEGY_MODE` at `rules-only`
  and run `python -m runner.evaluate`.
