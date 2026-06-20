"""Config-driven knobs for the risk gate.

A pydantic model (not a plain dataclass) so it composes directly into ``core.config.Settings``
and gets the same env-var validation. Every hard rule in the gate reads its threshold from
here — nothing is hardcoded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SizingMode = Literal["new-dollars", "target-weight"]


class RiskParams(BaseModel):
    """Thresholds the sovereign gate enforces. All values must be sane and positive."""

    model_config = {"frozen": True}

    # Max fraction of equity allowed in any single symbol (0 < x <= 1).
    max_position_pct: float = Field(default=0.20, gt=0.0, le=1.0)
    # Hard cap on total concurrent positions.
    max_positions: int = Field(default=10, gt=0)
    # Penny-stock floor: reject any symbol priced below this.
    min_price: float = Field(default=5.0, ge=0.0)
    # Liquidity floor: reject any symbol below this average dollar volume.
    min_avg_dollar_volume: float = Field(default=1_000_000.0, ge=0.0)
    # Daily loss limit as a positive fraction of equity. If the session loss exceeds
    # this, only sells/holds are permitted (no new risk).
    daily_loss_limit: float = Field(default=0.05, gt=0.0, le=1.0)

    # --- Buy-sizing semantics (default preserves the legacy alpha-path behavior) ---
    # "new-dollars": target_weight sizes *new* dollars to deploy (momentum/MR path).
    # "target-weight": target_weight is the desired *final* fraction; the gate nets it
    #   against the current holding (delta>0 buys, delta<0 partial-sells). Used by the
    #   ETF rebalancer so backtest and live size to the same targets.
    sizing: SizingMode = "new-dollars"
    # Min trade size (rebalance churn guard). Effective floor on |delta| dollars is
    # max(min_trade_dollars, min_trade_pct * equity); legs below it are skipped.
    min_trade_dollars: float = Field(default=0.0, ge=0.0)
    min_trade_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    # Max total traded notional (buys + sells) per gate call as a fraction of equity.
    # None = no cap (a full two-sided rebalance can legitimately exceed 1.0). When set and
    # exceeded, every sized leg is scaled down proportionally.
    max_turnover_pct: float | None = Field(default=None, gt=0.0)
