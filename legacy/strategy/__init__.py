"""Archived alpha strategy: the rules-only / LLM momentum + mean-reversion path.

Retired in the rebalancer pivot. ``strategy/strategy.py`` still dispatches to these for the
``rules-only`` and ``llm`` modes so the disproven behavior stays reproducible, but the core
loop now defaults to the ETF rebalancer (``strategy/rebalance.py``).
"""
