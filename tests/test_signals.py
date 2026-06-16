"""Tests for signals — hand-computed indicators, no-NaN/serializable, flags, determinism."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from core.config import SignalConfig
from core.contracts import AccountState, FilingFlags, NewsContext
from data.frame import COLUMNS, INDEX_NAME
from ingest.snapshot import MarketSnapshot
from signals import compute_signals
from signals.indicators import atr, roc, rsi, sma, zscore

_ACCOUNT = AccountState(cash=10_000.0, equity=10_000.0, buying_power=10_000.0)


def _frame(
    adj_close: list[float],
    *,
    adj_high: list[float] | None = None,
    adj_low: list[float] | None = None,
    close: list[float] | None = None,
    volume: float = 1_000_000.0,
) -> pd.DataFrame:
    n = len(adj_close)
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n), name=INDEX_NAME)
    base_close = close if close is not None else adj_close
    data = {c: list(base_close) for c in COLUMNS}
    data["volume"] = [volume] * n
    data["adj_volume"] = [volume] * n
    data["adj_close"] = list(adj_close)
    data["adj_high"] = list(adj_high) if adj_high is not None else list(adj_close)
    data["adj_low"] = list(adj_low) if adj_low is not None else list(adj_close)
    data["close"] = list(base_close)
    return pd.DataFrame(data, index=idx)


# --------------------------------------------------------------------------------------
# Hand-computed indicators
# --------------------------------------------------------------------------------------
def test_sma_hand_computed():
    df = _frame([10, 11, 12, 13, 14])
    assert sma(df, 3) == pytest.approx(13.0)   # mean(12,13,14)
    assert sma(df, 5) == pytest.approx(12.0)   # mean(10..14)


def test_roc_hand_computed():
    df = _frame([10, 11, 12, 13, 14])
    assert roc(df, 4) == pytest.approx(0.4)            # 14/10 - 1
    assert roc(df, 2) == pytest.approx(14 / 12 - 1)


def test_rsi_hand_computed_mixed():
    # deltas (last 5): [1,-1,1,-1,1] -> avg_gain=0.6, avg_loss=0.4, RS=1.5 -> RSI=60
    df = _frame([10, 11, 10, 11, 10, 11])
    assert rsi(df, 5) == pytest.approx(60.0)


def test_rsi_all_gains_is_100_flat_is_50():
    assert rsi(_frame([10, 11, 12, 13, 14]), 4) == pytest.approx(100.0)
    assert rsi(_frame([10, 10, 10, 10, 10]), 4) == pytest.approx(50.0)


def test_atr_hand_computed():
    df = _frame(
        adj_close=[10, 11, 12],
        adj_high=[11, 12, 13],
        adj_low=[9, 10, 11],
    )
    # TR = [2, 2, 2]; ATR over last 2 = 2.0
    assert atr(df, 2) == pytest.approx(2.0)


def test_zscore_hand_computed():
    df = _frame([10, 11, 12, 13, 14])
    # mean=12, sample std=sqrt(2.5)=1.58114; z=(14-12)/1.58114
    assert zscore(df, 5) == pytest.approx(2.0 / math.sqrt(2.5))


def test_zscore_flat_is_none():
    assert zscore(_frame([5, 5, 5, 5, 5]), 5) is None


def test_indicators_none_on_insufficient_history():
    df = _frame([10, 11, 12])
    assert sma(df, 5) is None
    assert roc(df, 5) is None
    assert rsi(df, 5) is None
    assert atr(df, 5) is None
    assert zscore(df, 5) is None


# --------------------------------------------------------------------------------------
# compute_signals
# --------------------------------------------------------------------------------------
def _rising_snapshot(n: int = 70) -> MarketSnapshot:
    prices = {"AAA": _frame([100.0 + i for i in range(n)])}
    return MarketSnapshot(as_of=pd.Timestamp("2024-06-01").date(),
                          prices=prices, account=_ACCOUNT)


def test_no_nan_and_json_serializable():
    snap = _rising_snapshot(70)
    sigs = compute_signals(snap, ["AAA"], SignalConfig())
    sig = sigs["AAA"]
    for value in sig.to_dict().values():
        assert not (isinstance(value, float) and math.isnan(value)), "NaN leaked"
    json.dumps(sig.to_dict())  # must not raise


def test_mixed_none_when_some_windows_dont_fit():
    # 30 bars: sma_short(20)/rsi(14)/atr(14)/zscore(20) fit; sma_long(50)/roc(60) don't.
    snap = _rising_snapshot(30)
    sig = compute_signals(snap, ["AAA"], SignalConfig())["AAA"]
    assert sig.sma_short is not None
    assert sig.rsi is not None
    assert sig.zscore is not None
    assert sig.sma_long is None
    assert sig.roc is None
    assert sig.trend_strength is None  # needs both SMAs


def test_flags_attached_and_default():
    prices = {
        "AAA": _frame([100.0 + i for i in range(70)]),
        "BBB": _frame([100.0 + i for i in range(70)]),
    }
    snap = MarketSnapshot(
        as_of=pd.Timestamp("2024-06-01").date(),
        prices=prices,
        account=_ACCOUNT,
        filings={"AAA": FilingFlags(recent_8k=True, recent_insider_buy=True)},
        news={"AAA": NewsContext(sentiment=0.4, headline_count=2)},
    )
    sigs = compute_signals(snap, ["AAA", "BBB"], SignalConfig())
    assert sigs["AAA"].recent_8k is True
    assert sigs["AAA"].recent_insider_buy is True
    assert sigs["AAA"].news_sentiment == pytest.approx(0.4)
    # BBB has no flags -> defaults.
    assert sigs["BBB"].recent_8k is False
    assert sigs["BBB"].news_sentiment is None


def test_candidate_filtering_and_determinism():
    snap = _rising_snapshot(70)
    # Only requested + present candidates appear; unknown symbols skipped.
    sigs = compute_signals(snap, ["AAA", "ZZZ"], SignalConfig())
    assert set(sigs.keys()) == {"AAA"}
    again = compute_signals(snap, ["AAA", "ZZZ"], SignalConfig())
    assert sigs["AAA"].to_dict() == again["AAA"].to_dict()
