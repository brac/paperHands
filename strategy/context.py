"""StrategyContext — bundles mode + config + the injected LLM client.

Passing this single object into ``propose_plan`` keeps the engine/risk code identical when
the mode flips: the only thing that changes between rules and llm is inside ``propose_plan``.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import (
    RebalanceConfig,
    Settings,
    StrategyConfig,
    StrategyMode,
    YoloConfig,
)
from strategy.client import LLMClient


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything ``propose_plan`` needs beyond the per-cycle inputs."""

    mode: StrategyMode
    config: StrategyConfig
    llm_client: LLMClient | None = None  # required only in llm mode
    rebalance: RebalanceConfig | None = None  # required only in rebalance mode
    yolo: YoloConfig | None = None  # required only in yolo mode


def build_strategy_context(
    settings: Settings, llm_client: LLMClient | None = None
) -> StrategyContext:
    """Composition-root factory: mode + knobs from config, client injected by the caller."""
    return StrategyContext(
        mode=settings.strategy_mode,
        config=settings.strategy,
        llm_client=llm_client,
        rebalance=settings.rebalance,
        yolo=settings.yolo,
    )
