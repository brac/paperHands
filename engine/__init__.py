"""Backtest engine: a thin event-driven loop that drives the pipeline over history.

Public surface: the ``Engine`` Protocol, the hand-rolled ``BacktestEngine``, its
``build_engine`` factory, and the ``BacktestResult`` / ``StepRecord`` contracts. A
backtrader-backed adapter could be added later behind the same Protocol without touching the
strategy/risk brain.
"""

from engine.engine import BacktestEngine, Engine
from engine.factory import build_engine
from engine.result import BacktestResult, StepRecord

__all__ = ["Engine", "BacktestEngine", "build_engine", "BacktestResult", "StepRecord"]
