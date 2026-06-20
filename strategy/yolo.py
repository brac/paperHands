"""YOLO-mode strategy: a max-risk, momentum-and-hype-chasing target-weight proposer.

The honest *contrast* to the rebalancer. Where the rebalancer holds a fixed basket to match
SPY with less risk, this sleeve ranks the whole candidate universe by a 'hype' score each
cycle and piles into the hottest ``top_n`` names, concentrated. It exists to draw a third line
on the graph — to *show* how much wilder hype-chasing is than the rebalancer or SPY — and is
paper-only, never promoted to live.

Slice 1 trades a **point-in-time-safe price/volume hype proxy** (rate-of-change + volume spike
+ breakout proximity), all precomputed in ``SignalSet``. Real social inputs (``social_score``,
``trump_mention``, ``reddit_mentions``) are blended in via ``social_weight`` once a feed is
wired; until then they are null and contribute nothing, and the line is labeled "proxy hype".

Like ``rebalance_propose`` this is a PURE function emitting target-weight orders the sovereign
gate sizes as deltas (``RiskParams.sizing="target-weight"``), so the same code runs in backtest
and live with no brain fork. The alpha-doctrine guards in ``propose_plan`` are deliberately
skipped for this mode (they would delete every momentum buy); the gate remains the final,
deterministic authority — this sleeve only *widens* its caps, never relaxes its hard rules.

The regime de-risk overlay is intentionally **ignored**: YOLO leans into momentum rather than
fading a downtrend. The ``regime`` argument is accepted (uniform ``propose_plan`` signature)
and unused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.config import YoloConfig
from core.contracts import Position, ProposedOrder, ProposedPlan
from signals.signalset import SignalSet
from strategy.regime import MarketRegime


def _component(value: float | None) -> float:
    """A hype component contributes only its non-negative part (None -> 0).

    Hype is one-sided: a falling name or below-average volume is not 'hot'. Clamping at 0 keeps
    a deeply negative roc from cancelling a strong volume spike — we rank attention, not value.
    """
    return value if value is not None and value > 0.0 else 0.0


def hype_score(signal: SignalSet, config: YoloConfig) -> float:
    """Blend a single name's clamped hype components into one rank-able score.

    ``dist_from_high`` is in ``(-1, 0]`` (0 == at the rolling high); ``1 + dist_from_high`` maps
    it to ``(0, 1]`` so names pinned at their highs score the breakout term fully. All other
    components are clamped to their non-negative part. Pure and deterministic.
    """
    breakout = 0.0
    if signal.dist_from_high is not None:
        breakout = max(0.0, 1.0 + signal.dist_from_high)
    return (
        config.momentum_weight * _component(signal.roc)
        + config.volume_weight * _component(signal.volume_spike)
        + config.breakout_weight * breakout
        + config.social_weight * _component(signal.social_score)
    )


def _live_price(signal: SignalSet | None) -> float | None:
    """The tradeable current price for a name, or None if it has no positive price."""
    price = signal.price if signal is not None else None
    return price if price is not None and price > 0.0 else None


def yolo_propose(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    config: YoloConfig,
    *,
    regime: MarketRegime | None = None,
) -> ProposedPlan:
    """Propose a concentrated target-weight basket of the hottest ``top_n`` names.

    Ranks every priced candidate by ``hype_score``, keeps the top ``top_n`` with a strictly
    positive score, and assigns target weights — conviction-weighted by score (or equal-weight)
    — each capped at ``max_position_pct`` and normalized so the book is ~fully deployed. Held
    names that fall out of the new top set get a full-close ``sell`` (the gate's all-or-nothing
    sell path); an optional ``stop_loss_pct`` force-exits a held name underwater past the
    threshold even if it is still hot.
    """
    equity = cash
    for p in positions:
        price = _live_price(signals.get(p.symbol))
        if price is not None and p.qty > 0:
            equity += p.qty * price
    if equity <= 0.0:
        return ProposedPlan()

    held = {p.symbol: p for p in positions}

    # Rank priced candidates by hype; keep the hottest top_n with a positive score. Ties break
    # on symbol for determinism (identical in backtest and live).
    ranked = sorted(
        ((sym, hype_score(sig, config))
         for sym, sig in signals.items() if _live_price(sig) is not None),
        key=lambda item: (-item[1], item[0]),
    )
    winners = [sym for sym, score in ranked[: config.top_n] if score > 0.0]

    # Stop-loss: a held name underwater past the threshold is exited regardless of its heat.
    stopped: set[str] = set()
    if config.stop_loss_pct is not None:
        for sym, pos in held.items():
            price = _live_price(signals.get(sym))
            if price is None or pos.avg_price <= 0.0:
                continue
            if price <= pos.avg_price * (1.0 - config.stop_loss_pct):
                stopped.add(sym)

    targets = _target_weights([s for s in winners if s not in stopped], signals, config)

    orders: list[ProposedOrder] = []
    for symbol in sorted(targets):
        weight = targets[symbol]
        orders.append(
            ProposedOrder(
                action="buy",
                symbol=symbol,
                target_weight=weight,
                conviction=weight,
                reason=f"yolo: chase hype to {weight:.0%}",
            )
        )
    # Full-close any held name not in the new target basket (rotated out or stopped).
    for symbol in sorted(held):
        if symbol in targets:
            continue
        if held[symbol].qty <= 0 or _live_price(signals.get(symbol)) is None:
            continue
        reason = "yolo: stop-loss exit" if symbol in stopped else "yolo: rotate out of cold name"
        orders.append(
            ProposedOrder(action="sell", symbol=symbol, target_weight=0.0, reason=reason)
        )

    return ProposedPlan(orders=tuple(orders))


def _target_weights(
    winners: Sequence[str], signals: Mapping[str, SignalSet], config: YoloConfig
) -> dict[str, float]:
    """Map the winning symbols to capped, normalized target weights (~fully deployed).

    Conviction mode weights by hype score; otherwise equal-weight. Every weight is capped at
    ``max_position_pct`` and the set is scaled to sum to 1.0 when possible — but the cap wins,
    so a very concentrated book may sum to less than 1.0 (the remainder stays cash). Returns an
    empty mapping when there are no winners (the sleeve goes fully to cash — nothing was hot).
    """
    if not winners:
        return {}

    cap = config.max_position_pct
    if config.conviction_weighted:
        raw = {s: hype_score(signals[s], config) for s in winners}
        total = sum(raw.values())
        weights = (
            {s: v / total for s, v in raw.items()}
            if total > 0.0
            else {s: 1.0 / len(winners) for s in winners}
        )
    else:
        weights = {s: 1.0 / len(winners) for s in winners}

    # Apply the per-name cap, then redistribute any leftover headroom to uncapped names so the
    # book stays as deployed as the cap allows (a few passes converge; bounded by name count).
    weights = {s: min(w, cap) for s, w in weights.items()}
    for _ in range(len(winners)):
        deployed = sum(weights.values())
        slack = 1.0 - deployed
        uncapped = [s for s, w in weights.items() if w < cap - 1e-9]
        if slack <= 1e-9 or not uncapped:
            break
        bump = slack / len(uncapped)
        weights = {
            s: (min(w + bump, cap) if s in uncapped else w) for s, w in weights.items()
        }
    return weights
