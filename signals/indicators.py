"""Pure technical-indicator functions over a canonical bar frame.

Each takes a ``data.frame``-shaped DataFrame (+ a window) and returns ``float | None`` —
``None`` whenever there is insufficient history or a degenerate divisor, so nothing NaN ever
leaks downstream. All math is on the **adjusted** series (split/dividend-correct); formulas
are deliberately simple and explicit so they can be verified against hand calculations.
"""

from __future__ import annotations

import math

import pandas as pd


def _finite(value: float) -> float | None:
    """Return the value only if it is a real finite number, else None."""
    return value if math.isfinite(value) else None


def sma(df: pd.DataFrame, window: int, column: str = "adj_close") -> float | None:
    """Simple moving average of the last ``window`` values of ``column``."""
    s = df[column]
    if window <= 0 or len(s) < window:
        return None
    return _finite(float(s.iloc[-window:].mean()))


def roc(df: pd.DataFrame, window: int, column: str = "adj_close") -> float | None:
    """Rate of change: ``value[-1] / value[-1-window] - 1``."""
    s = df[column]
    if window <= 0 or len(s) < window + 1:
        return None
    prior = float(s.iloc[-1 - window])
    if prior == 0.0:
        return None
    return _finite(float(s.iloc[-1]) / prior - 1.0)


def rsi(df: pd.DataFrame, window: int, column: str = "adj_close") -> float | None:
    """RSI (simple-average / Cutler variant) over the last ``window`` deltas.

    ``avg_gain`` and ``avg_loss`` are the means of the positive and absolute-negative
    one-step changes. RSI = 100 - 100/(1 + avg_gain/avg_loss). If there are no losses, RSI is
    100 (or 50 when the window is perfectly flat); this keeps the result finite and defined.
    """
    s = df[column]
    if window <= 0 or len(s) < window + 1:
        return None
    deltas = s.diff().iloc[-window:]
    avg_gain = float(deltas.clip(lower=0.0).mean())
    avg_loss = float((-deltas).clip(lower=0.0).mean())
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return _finite(100.0 - 100.0 / (1.0 + rs))


def atr(df: pd.DataFrame, window: int) -> float | None:
    """Average True Range (absolute) over the last ``window`` bars, on adjusted OHLC."""
    if window <= 0 or len(df) < window + 1:
        return None
    high = df["adj_high"]
    low = df["adj_low"]
    prev_close = df["adj_close"].shift(1)
    true_range = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _finite(float(true_range.iloc[-window:].mean()))


def zscore(df: pd.DataFrame, window: int, column: str = "adj_close") -> float | None:
    """Mean-reversion z-score: ``(value[-1] - mean) / std`` over ``window`` (sample std)."""
    s = df[column]
    if window < 2 or len(s) < window:
        return None
    win = s.iloc[-window:]
    std = float(win.std(ddof=1))
    if std == 0.0:
        return None
    return _finite((float(win.iloc[-1]) - float(win.mean())) / std)
