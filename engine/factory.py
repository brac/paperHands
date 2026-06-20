"""Composition root for the backtest engine — wires every injected dependency from config."""

from __future__ import annotations

from broker.simulated import SimulatedBroker
from core.config import Settings, apply_mode_requirements
from data import build_data_provider
from data.base import DataProvider
from engine.engine import BacktestEngine
from ingest import build_snapshot_assembler
from screen import build_universe_provider
from strategy import LLMClient, build_strategy_context


def build_engine(
    settings: Settings,
    *,
    provider: DataProvider | None = None,
    llm_client: LLMClient | None = None,
) -> BacktestEngine:
    """Construct a fully-wired BacktestEngine from settings (+ an optional LLM client).

    A ``provider`` may be passed to share one data provider (and its warm cache) across many
    engines — e.g. a multi-window evaluation. Each call still gets a fresh ``SimulatedBroker``.
    """
    settings = apply_mode_requirements(settings)  # rebalance needs target-weight + screen bypass
    provider = provider or build_data_provider(settings)
    return BacktestEngine(
        provider,
        build_snapshot_assembler(settings, provider),
        build_universe_provider(settings),
        screen_config=settings.screen,
        signal_config=settings.signals,
        strategy_ctx=build_strategy_context(settings, llm_client),
        risk_params=settings.risk,
        broker=SimulatedBroker(settings.broker),
        config=settings.engine,
    )
