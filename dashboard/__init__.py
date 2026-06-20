"""Read-only check-in dashboard for the ETF rebalancer.

``export`` dumps a recorded backtest run from the SQLite store (``record/``) into a single
static JSON file that the Vite/React SPA in ``dashboard/web`` renders. It is strictly
read-only — it reports (equity vs SPY, positions-vs-target drift, rebalance trades + realized
gain/loss, risk stats); it never trades. Honest framing: the benchmark is SPY and the goal is
to match it with less risk, not to beat it.
"""
