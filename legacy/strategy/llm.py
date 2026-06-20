"""LLM-mode strategy: build a JSON-only prompt, call the injected client, parse robustly.

The doctrine (technicals primary; news secondary; JSON only) is stated in the system prompt,
but never *trusted* — ``parse_plan`` validates/coerces every item and returns a safe empty
plan on any malformed output, and the shared ``enforce_technicals_primary`` guard (applied by
``propose_plan``) drops any buy the model invented without technical support. Pure except the
injected ``client.complete`` call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from core.config import StrategyConfig
from core.contracts import Position, ProposedOrder, ProposedPlan, _is_finite_number
from signals.signalset import SignalSet

if TYPE_CHECKING:
    # Type-only import: a runtime import would form a cycle (strategy/__init__ pulls
    # strategy.strategy, which imports this archived module back). Annotations are lazy
    # strings (from __future__ import annotations), so this is never evaluated at runtime.
    from strategy.client import LLMClient

_VALID_ACTIONS = {"buy", "sell", "hold"}
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_SYSTEM_PROMPT = """\
You are the strategy layer of a disciplined, rules-based trading system.

Doctrine (do not deviate):
- Technical indicators are the PRIMARY driver of every decision.
- News and SEC-filing flags are SECONDARY: they may raise or lower your conviction, or veto
  a buy, but they may NEVER originate a trade on their own. A buy must be justified by
  technicals first.
- Manage risk: do not over-concentrate; prefer high-conviction names.

Respond with JSON ONLY — no prose, no markdown fences. The response must be a JSON array of
order objects, each: {"action": "buy"|"sell"|"hold", "symbol": str, "target_weight": number
(fraction of equity, 0..1), "conviction": number (0..1), "reason": str}. Return [] if there
is nothing to do."""


def build_prompt(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    config: StrategyConfig,
) -> tuple[str, str]:
    """Return (system, user) prompts. The user prompt is the serialized decision context."""
    payload = {
        "cash": cash,
        "max_new_positions": config.max_new_positions,
        "max_target_weight": config.max_target_weight,
        "positions": [{"symbol": p.symbol, "qty": p.qty} for p in positions],
        "signals": [signals[s].to_dict() for s in signals],
    }
    user = (
        "Here is the current decision context as JSON. Propose orders per the doctrine.\n\n"
        + json.dumps(payload, sort_keys=True)
    )
    return _SYSTEM_PROMPT, user


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _coerce_order(item: Any) -> ProposedOrder | None:
    """Validate/coerce one raw item into a ProposedOrder, or None if unusable."""
    if not isinstance(item, dict):
        return None
    action = item.get("action")
    if action not in _VALID_ACTIONS:
        return None
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return None

    raw_weight = item.get("target_weight", 0.0)
    target_weight = float(raw_weight) if _is_finite_number(raw_weight) else 0.0
    target_weight = max(0.0, target_weight)

    raw_conviction = item.get("conviction", 0.0)
    conviction = float(raw_conviction) if _is_finite_number(raw_conviction) else 0.0

    reason = item.get("reason", "")
    reason = reason if isinstance(reason, str) else ""

    return ProposedOrder(
        action=action,
        symbol=symbol,
        target_weight=target_weight,
        conviction=conviction,
        reason=reason,
    )


def parse_plan(text: str) -> ProposedPlan:
    """Parse model text into a ProposedPlan. Never raises; returns an empty plan on failure."""
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError):
        return ProposedPlan()
    if isinstance(data, dict) and "orders" in data:
        data = data["orders"]
    if not isinstance(data, list):
        return ProposedPlan()
    orders = [o for o in (_coerce_order(item) for item in data) if o is not None]
    return ProposedPlan(orders=tuple(orders))


def llm_propose(
    signals: Mapping[str, SignalSet],
    positions: Sequence[Position],
    cash: float,
    config: StrategyConfig,
    client: LLMClient,
) -> ProposedPlan:
    """Build the prompt, call the injected client, and robustly parse the response."""
    system, user = build_prompt(signals, positions, cash, config)
    try:
        text = client.complete(system, user)
    except Exception:  # noqa: BLE001 - a flaky client must never crash a cycle
        return ProposedPlan()
    return parse_plan(text)
