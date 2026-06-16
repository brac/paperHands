"""Record & benchmark: persist runs and report portfolio-vs-SPY performance.

Public surface: ``record_run`` (compute benchmark + stats + persist), the ``BacktestStore``,
the ``RunSummary`` / ``PerformanceStats`` contracts, ``compute_stats``, and ``format_report``.
"""

from record.recorder import record_run
from record.report import format_report
from record.stats import PerformanceStats, compute_stats
from record.store import BacktestStore, new_run_id
from record.summary import RunSummary

__all__ = [
    "record_run",
    "format_report",
    "BacktestStore",
    "new_run_id",
    "RunSummary",
    "PerformanceStats",
    "compute_stats",
]
