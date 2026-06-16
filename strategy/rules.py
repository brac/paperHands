"""Rules-only strategy: deterministic, pure proposal logic over SignalSets.

Technicals originate every buy (momentum or mean-reversion regime); news/filing flags only
boost conviction or veto — never originate. Sizing is conviction-weighted and clamped to a
strategy-level cap (the sovereign gate still enforces the hard caps). No network, no clock —
identical input yields identical output, so it is safe for bulk historical sweeps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.config import StrategyConfig
from core.contracts import Position, ProposedOrder, ProposedPlan
from signals.signalset import SignalSet
from strategy.guard import has_technical_support

# Conviction full-scale references (a value at/above scale maps to conviction 1.0).
_MOMENTUM_FULL_SCALE_ROC = 0.20  # +20% ROC -> full momentum conviction
_ZSCORE_FULL_SCALE = 3.0  # zscore of -3 -> full mean-reversion conviction


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _buy_conviction(signal: SignalSet, config: StrategyConfig) -> float | None:
    """Conviction in (0, 1] for a buy, or None if not a (surviving) buy candidate.

    Momentum conviction is suppressed when RSI is overbought; a buy is vetoed when sentiment
    is at/below the veto threshold; positive news adds a conviction boost.
    """
    if not has_technical_support(signal, config):
        return None

    # Volatility filter (off by default): don't buy names whose ATR% exceeds the cap.
    if (
        config.max_atr_pct is not None
        and signal.atr_pct is not None
        and signal.atr_pct > config.max_atr_pct
    ):
        return None

    momentum = 0.0
    if (
        signal.roc is not None
        and signal.trend_strength is not None
        and signal.roc > config.momentum_buy_threshold
        and signal.trend_strength > 0.0
    ):
        momentum = _clamp01(signal.roc / _MOMENTUM_FULL_SCALE_ROC)
        if signal.rsi is not None and signal.rsi > config.rsi_overbought:
            momentum = 0.0  # don't chase an overbought name

    mean_reversion = 0.0
    if signal.zscore is not None and signal.zscore < config.zscore_oversold:
        mean_reversion = _clamp01(-signal.zscore / _ZSCORE_FULL_SCALE)

    conviction = max(momentum, mean_reversion)
    if conviction <= 0.0:
        return None  # support existed but RSI suppressed the only regime

    # Momentum-quality blend (off by default): down-weight names far below their rolling high.
    if config.high_proximity_weight > 0.0 and signal.dist_from_high is not None:
        w = config.high_proximity_weight
        proximity = _clamp01(1.0 + signal.dist_from_high)  # dist in (-1, 0] -> proximity in [0, 1]
        conviction = _clamp01(conviction * ((1.0 - w) + w * proximity))
        if conviction <= 0.0:
            return None

    # News is secondary: it may veto or boost, never originate (support already required).
    if signal.news_sentiment is not None and signal.news_sentiment <= config.news_veto_sentiment:
        return None
    news_positive = (
        signal.recent_8k
        or signal.recent_insider_buy
        or (signal.news_sentiment is not None and signal.news_sentiment > 0.0)
    )
    if news_positive:
        conviction = _clamp01(conviction + config.news_conviction_boost)
    return conviction


def _is_bearish(signal: SignalSet, config: StrategyConfig) -> bool:
    return (signal.trend_strength is not None and signal.trend_strength < 0.0) or (
        signal.roc is not None and signal.roc < config.sell_threshold
    )


def rules_propose(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    config: StrategyConfig,
) -> ProposedPlan:
    """Propose buys (top-conviction technical candidates) and sells (bearish held names)."""
    orders: list[ProposedOrder] = []

    # Buys — only when there is cash to deploy.
    candidates: list[tuple[str, float]] = []
    if cash > 0.0:
        for symbol, signal in signals.items():
            conviction = _buy_conviction(signal, config)
            if conviction is not None:
                candidates.append((symbol, conviction))
        candidates.sort(key=lambda r: (-r[1], r[0]))  # conviction desc, symbol asc
        candidates = candidates[: config.max_new_positions]

    buy_symbols = {symbol for symbol, _ in candidates}
    for symbol, conviction in candidates:
        orders.append(
            ProposedOrder(
                action="buy",
                symbol=symbol,
                target_weight=conviction * config.max_target_weight,
                conviction=conviction,
                reason=f"technical conviction {conviction:.2f}",
            )
        )

    # Sells — held names (with a fresh signal) that turned bearish and aren't being bought.
    for position in sorted(positions, key=lambda p: p.symbol):
        symbol = position.symbol
        if symbol in buy_symbols:
            continue
        held_signal = signals.get(symbol)
        if held_signal is None:
            continue  # no fresh signal -> hold (do nothing)
        if _is_bearish(held_signal, config):
            orders.append(
                ProposedOrder(
                    action="sell",
                    symbol=symbol,
                    target_weight=0.0,
                    conviction=0.0,
                    reason="bearish technicals",
                )
            )

    return ProposedPlan(orders=tuple(orders))
