"""Orchestration: single-run backtest + multi-window evaluation.

Public surface: ``run_backtest`` (one run -> recorded RunSummary) and the ``Window`` /
``DEFAULT_WINDOWS`` regime set. The evaluation API lives in ``runner.evaluate`` and the CLIs
in ``runner.backtest`` / ``runner.evaluate`` — deliberately NOT imported here, so that
``python -m runner.evaluate`` doesn't trip runpy's "already in sys.modules" warning (a package
must not import the submodule that's being run as ``__main__``).
"""

from runner.run import run_backtest
from runner.windows import DEFAULT_WINDOWS, Window

__all__ = ["run_backtest", "Window", "DEFAULT_WINDOWS"]
