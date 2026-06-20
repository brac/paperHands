"""Tests for the runner — single-run orchestration, multi-window evaluation, config, CLIs.

Offline: a FakeProvider over the real seed symbols (AAPL/MSFT) + SPY, so the real
StaticUniverseProvider supplies metadata. Windows sit inside the fake series' range.
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

from core.config import RecordConfig, Settings
from data.frame import COLUMNS, INDEX_NAME, empty_bars
from engine import build_engine
from record import BacktestStore, format_report
from record.stats import PerformanceStats
from record.summary import RunSummary
from runner import run_backtest
from runner.config import resolve_run_config
from runner.evaluate import evaluate, format_evaluation
from runner.windows import Window


def _series(base: float, slope: float = 0.3) -> pd.DataFrame:
    idx = pd.bdate_range("2023-06-01", "2024-01-10")
    vals = [base + slope * i for i in range(len(idx))]
    data = {c: list(vals) for c in COLUMNS}
    data["volume"] = [2_000_000.0] * len(idx)
    data["adj_volume"] = [2_000_000.0] * len(idx)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name=INDEX_NAME))


class _FakeProvider:
    def __init__(self) -> None:
        self._frames = {
            "AAPL": _series(150.0), "MSFT": _series(300.0), "SPY": _series(400.0),
        }

    def get_daily_bars(self, symbol, start, end, *, as_of=None):
        df = self._frames.get(symbol)
        if df is None:
            return empty_bars()
        eff_end = end if as_of is None else min(end, as_of)
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(eff_end))]


def _settings(tmp_path) -> Settings:
    return Settings(record=RecordConfig(db_path=str(tmp_path / "r.sqlite")))


_W1 = Window("nov", date(2023, 11, 1), date(2023, 11, 30), "chop")
_W2 = Window("dec", date(2023, 12, 1), date(2023, 12, 29), "chop")


def test_run_backtest_end_to_end(tmp_path):
    settings = _settings(tmp_path)
    store = BacktestStore(settings.record.db_path)
    summary = run_backtest(
        settings, _W1.start, _W1.end, ["AAPL", "MSFT"],
        provider=_FakeProvider(), store=store,
    )
    assert isinstance(summary, RunSummary)
    assert summary.start == _W1.start and summary.end == _W1.end
    assert summary.portfolio_final == pytest.approx(settings.broker.starting_cash)  # no buys
    assert summary.benchmark_final > settings.broker.starting_cash  # SPY rose
    assert store.load_summary(summary.run_id) == summary
    assert "Portfolio" in format_report(summary)


def test_evaluate_aggregates_windows(tmp_path):
    settings = _settings(tmp_path)
    store = BacktestStore(settings.record.db_path)
    result = evaluate(
        settings, (_W1, _W2), ["AAPL", "MSFT"], provider=_FakeProvider(), store=store,
    )
    assert len(result.outcomes) == 2
    assert all(o.ok for o in result.outcomes)
    assert result.universe_size == 2
    text = format_evaluation(result)
    assert "nov" in text and "dec" in text and "Aggregate" in text
    assert set(store.list_runs()) >= {"nov", "dec"}


def _canned_summary(run_id: str) -> RunSummary:
    pstats = PerformanceStats(0.10, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0)
    bstats = PerformanceStats(0.05, 0.0, 0.0, 0.0, 0.4, 0.0, 0.0)
    return RunSummary(
        run_id, date(2021, 1, 1), date(2021, 12, 31), 100_000.0, "rules-only",
        1, 1, 110_000.0, 105_000.0, pstats, bstats,
    )


def test_evaluate_isolates_window_failure(tmp_path, monkeypatch):
    bad = Window("bad", date(2099, 1, 1), date(2099, 2, 1), "future")

    def _fake_run(settings, start, end, universe=None, **kw):
        if start == bad.start:
            raise RuntimeError("boom")
        return _canned_summary(kw.get("run_id") or "ok")

    import runner.evaluate  # noqa: F401 - ensure the module is imported
    monkeypatch.setattr(sys.modules["runner.evaluate"], "run_backtest", _fake_run)
    result = evaluate(
        _settings(tmp_path), (_W1, bad), ["AAPL"],
        provider=_FakeProvider(), store=BacktestStore(_settings(tmp_path).record.db_path),
    )
    assert [o.ok for o in result.outcomes] == [True, False]
    assert "boom" in (result.outcomes[1].error or "")
    assert len(result.successful()) == 1
    assert "FAILED" in format_evaluation(result)


def test_build_engine_shares_provider(tmp_path):
    settings = _settings(tmp_path)
    provider = _FakeProvider()
    e1 = build_engine(settings, provider=provider)
    e2 = build_engine(settings, provider=provider)
    assert e1._provider is provider  # shared
    assert e1._broker is not e2._broker  # but a fresh broker each time


def test_resolve_run_config_flag_overrides_file(tmp_path):
    cfg_path = tmp_path / "run.json"
    cfg_path.write_text(
        '{"start": "2024-01-01", "end": "2024-02-01", "universe": "AAA,BBB", "mode": "rules-only"}'
    )
    cfg = resolve_run_config(
        config_path=str(cfg_path), start="2024-03-01", end=None,
        universe=None, mode=None, label=None,
    )
    assert cfg.start == date(2024, 3, 1)  # flag wins
    assert cfg.end == date(2024, 2, 1)    # from file
    assert cfg.universe == ("AAA", "BBB")
    assert cfg.mode == "rules-only"


def test_resolve_run_config_requires_dates():
    with pytest.raises(ValueError):
        resolve_run_config(
            config_path=None, start="2024-01-01", end=None,
            universe=None, mode=None, label=None,
        )
