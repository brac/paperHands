"""The swappable strategy core: dual-mode ``propose_plan`` + the injected LLM client.

The strategy proposes; the sovereign risk gate disposes. ``propose_plan`` is pure except for
the injected ``LLMClient`` (llm mode only), so the same code runs in backtest, paper, and
live — no brain fork. Technicals are primary; news/filing flags only modulate or veto.
"""

from strategy.anthropic_client import AnthropicClient, build_anthropic_client
from strategy.client import LLMClient
from strategy.context import StrategyContext, build_strategy_context
from strategy.regime import MarketRegime, compute_market_regime
from strategy.strategy import propose_plan

__all__ = [
    "AnthropicClient",
    "LLMClient",
    "MarketRegime",
    "StrategyContext",
    "build_anthropic_client",
    "build_strategy_context",
    "compute_market_regime",
    "propose_plan",
]
