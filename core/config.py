"""Application configuration, loaded once from env/.env and injected downstream.

Built on pydantic-settings so types are validated at load time. Pure modules never read
this directly — the composition root (`runner/`) constructs it and passes the pieces it
needs (e.g. a ``RiskParams``) into pure functions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from risk.params import RiskParams

StrategyMode = Literal["rules-only", "llm", "rebalance", "yolo"]
RebalanceTrigger = Literal["drift", "schedule", "both"]
DataProviderName = Literal["tiingo"]
UniverseSource = Literal["static", "tiingo"]


class DataConfig(BaseModel):
    """Historical data provider + local cache settings.

    Env vars: ``PAPERHANDS_DATA__PROVIDER``, ``PAPERHANDS_DATA__CACHE_DIR``,
    ``PAPERHANDS_DATA__TIINGO_BASE_URL``. Only Tiingo is implemented this slice; the
    ``provider`` field is the swappable seam for Polygon later.
    """

    model_config = {"frozen": True}

    provider: DataProviderName = "tiingo"
    cache_dir: str = "data_cache"
    tiingo_base_url: str = "https://api.tiingo.com"


class IngestConfig(BaseModel):
    """Snapshot-assembly settings.

    Env var: ``PAPERHANDS_INGEST__HISTORY_DAYS``. ``history_days`` is the calendar lookback
    of price history attached per symbol; 600 (~415 trading days) gives later signals room
    for long moving averages.
    """

    model_config = {"frozen": True}

    history_days: int = Field(default=600, gt=0)

    # Secondary SEC-EDGAR filings feed (off by default; technicals stay primary regardless).
    filings_enabled: bool = False
    filings_recency_days: int = Field(default=5, gt=0)  # an 8-K/Form-4 within N days = "recent"
    edgar_user_agent: str = ""  # SEC requires a descriptive contact UA; 403 without it
    edgar_cache_dir: str = "data_cache"


class SocialConfig(BaseModel):
    """Exotic 'hype' feed settings — the YOLO sleeve's real social input (Reddit-WSB).

    Env-prefixed ``PAPERHANDS_SOCIAL__``. Off by default; the YOLO proxy needs no feed. When
    ``enabled``, the snapshot attaches per-symbol ``HypeContext`` from a **precomputed, point-in-
    time aggregate** of a WSB post archive (built once via ``python -m ingest.wsb``). The feed
    only ever reads days at-or-before the decision date, so it obeys the same no-look-ahead rule
    as prices — provided the archive's timestamps are original post times (an edited/backfilled
    scrape would reintroduce look-ahead; documented risk).

    ``wsb_csv_path`` is the raw archive (title,score,id,url,comms_num,created,body,timestamp);
    ``aggregate_path`` is the compact SQLite the builder writes and the feed reads.
    ``mention_window`` is the trailing window (days) summed into the hype score / mention count;
    ``velocity_window`` is the short window compared against the prior equal window for velocity.
    """

    model_config = {"frozen": True}

    enabled: bool = False
    source: Literal["wsb"] = "wsb"
    wsb_csv_path: str = ""
    aggregate_path: str = "data_cache/wsb_daily.sqlite"
    mention_window: int = Field(default=7, gt=0)
    velocity_window: int = Field(default=3, gt=0)
    # Drop posts below this reddit score at build time (noise floor; 0 keeps everything).
    min_post_score: int = 0


class UniverseConfig(BaseModel):
    """Base-universe source (env-prefixed ``PAPERHANDS_UNIVERSE__``).

    ``static`` is the committed large-cap seed CSV. ``tiingo`` builds a survivorship-aware
    small-cap universe from Tiingo's supported-tickers list (active + delisted), bounded to
    ``max_symbols`` by a deterministic, survivorship-neutral selection.
    """

    model_config = {"frozen": True}

    source: UniverseSource = "static"
    max_symbols: int = Field(default=750, gt=0)
    exchanges: tuple[str, ...] = ("NYSE", "NASDAQ", "NYSE MKT", "AMEX")
    cache_dir: str = "data_cache"


class ScreenConfig(BaseModel):
    """User-tunable screen knobs — the user's sole control point over the candidate set.

    Env vars are prefixed ``PAPERHANDS_SCREEN__`` (e.g. ``PAPERHANDS_SCREEN__MIN_PRICE``);
    list knobs are given as JSON arrays (e.g. ``PAPERHANDS_SCREEN__SECTORS_EXCLUDE=["Energy"]``).

    A market-cap floor is deliberately absent: Tiingo EOD carries no point-in-time
    shares-outstanding, so any cap filter would be a look-ahead/staleness approximation. The
    shipped knobs are only those price/volume + static metadata can honestly support.
    """

    model_config = {"frozen": True}

    # Hard tradeability floors (applied to every symbol, watchlist included).
    min_avg_dollar_volume: float = Field(default=1e7, ge=0.0)
    min_price: float = Field(default=5.0, ge=0.0)

    # Sector filter (skipped for watchlist symbols). Empty include = "all sectors".
    sectors_include: tuple[str, ...] = ()
    sectors_exclude: tuple[str, ...] = ()

    # Pinned symbols: bypass the sector filter and the top-N cut (but not the hard floors).
    watchlist: tuple[str, ...] = ()

    # Windows (in bars) for the liquidity and momentum computations.
    liquidity_window: int = Field(default=20, gt=0)
    momentum_window: int = Field(default=60, gt=0)

    # Composite ranking: momentum is primary; news relevance only adjusts (default off).
    momentum_weight: float = 1.0
    relevance_weight: float = 0.0

    # Hard cap on the ranked candidate set.
    max_candidates: int = Field(default=20, gt=0)


class SignalConfig(BaseModel):
    """Technical-indicator window knobs (in bars).

    Env vars prefixed ``PAPERHANDS_SIGNALS__`` (e.g. ``PAPERHANDS_SIGNALS__RSI_WINDOW``).
    Indicators are computed on adjusted prices; a window that exceeds available history
    yields ``None`` (never NaN), so the output stays JSON-serializable.
    """

    model_config = {"frozen": True}

    sma_short_window: int = Field(default=20, gt=0)
    sma_long_window: int = Field(default=50, gt=0)
    rsi_window: int = Field(default=14, gt=0)
    roc_window: int = Field(default=60, gt=0)
    atr_window: int = Field(default=14, gt=0)
    zscore_window: int = Field(default=20, gt=0)
    high_window: int = Field(default=252, gt=0)  # rolling high for dist_from_high (~52 weeks)


class StrategyConfig(BaseModel):
    """Rules-mode strategy knobs (env-prefixed ``PAPERHANDS_STRATEGY__``).

    The sovereign risk gate enforces hard caps regardless; these only shape the *proposal*.
    Technicals are primary — news knobs may only boost/lower conviction or veto a buy.
    """

    model_config = {"frozen": True}

    # Sizing + breadth.
    max_new_positions: int = Field(default=5, gt=0)
    max_target_weight: float = Field(default=0.15, gt=0.0, le=1.0)  # <= risk max_position_pct

    # Buy regimes (a buy needs technical support: momentum OR mean-reversion).
    momentum_buy_threshold: float = 0.0  # roc must exceed this (with trend_strength > 0)
    zscore_oversold: float = -1.5  # buy when zscore is below this (mean-reversion)
    rsi_overbought: float = 70.0  # suppress momentum buys when rsi exceeds this

    # Optional conviction levers (Phase 3; default = off, so behavior is unchanged until tuned).
    max_atr_pct: float | None = Field(default=None, ge=0.0)  # skip buys above this volatility
    high_proximity_weight: float = Field(default=0.0, ge=0.0, le=1.0)  # blend in dist_from_high

    # Tier-1 risk overlays (default off/neutral; validated via walk-forward before adoption).
    regime_filter_enabled: bool = False  # drop new buys when the market is risk-off
    regime_ma_window: int = Field(default=200, gt=0)  # reference-index MA for risk-on/off
    momentum_rank_fraction: float = Field(default=1.0, gt=0.0, le=1.0)  # buy only top-frac by roc
    stop_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)  # exit a held name past -x%

    # Sell rule for held names whose signal turned bearish.
    sell_threshold: float = 0.0  # sell when roc < this or trend_strength < 0

    # News modulation (secondary; never originates a trade).
    news_conviction_boost: float = Field(default=0.1, ge=0.0)
    news_veto_sentiment: float = -0.5  # veto a buy when sentiment <= this


class RebalanceConfig(BaseModel):
    """Config-driven ETF rebalancer knobs (env-prefixed ``PAPERHANDS_REBALANCE__``).

    The hands-off core of the pivot: hold a fixed basket of ETFs at config-driven target
    weights and trade only when an asset drifts beyond a band or a schedule fires. Low
    turnover is a *feature* (fewer taxable events, less slippage). The risk gate sizes the
    actual deltas (it must run with ``RiskParams.sizing="target-weight"``).

    Weights are a JSON object, e.g.
    ``PAPERHANDS_REBALANCE__TARGET_WEIGHTS='{"SPY":0.6,"BND":0.3,"GLD":0.1}'``.
    Their sum may be < 1.0 (the remainder is intentional cash) but never > 1.0.
    """

    model_config = {"frozen": True}

    # Desired final fraction of equity per symbol. Each in (0, 1]; sum <= 1.0.
    target_weights: dict[str, float] = Field(
        default_factory=lambda: {"SPY": 0.6, "BND": 0.3, "GLD": 0.1}
    )
    # Asset the regime overlay rotates into when de-risking (empty = hold as cash).
    defensive_symbol: str = "BND"
    # Which targets count as "equity" for the regime de-risk overlay.
    equity_symbols: tuple[str, ...] = ("SPY",)

    # Trigger: "drift" acts only when |current-target| > drift_band for some asset;
    # "schedule" rebalances to target every time it is called (cadence = engine
    # rebalance_every_n_days); "both" = drift gate, then full rebalance.
    trigger: RebalanceTrigger = "drift"
    drift_band: float = Field(default=0.05, gt=0.0, le=1.0)

    # Regime de-risk overlay (RISK control, not a predictor; default off). When enabled and
    # the market is risk-off (SPY < its MA), scale equity weights by (1 - shift) and rotate
    # the freed weight into the defensive symbol (or cash). Never adds exposure.
    regime_derisk_enabled: bool = False
    regime_derisk_shift: float = Field(default=0.5, ge=0.0, le=1.0)
    regime_ma_window: int = Field(default=200, gt=0)

    @model_validator(mode="after")
    def _validate_weights(self) -> RebalanceConfig:
        if not self.target_weights:
            raise ValueError("rebalance target_weights must not be empty")
        for sym, w in self.target_weights.items():
            if not sym:
                raise ValueError("rebalance target_weights has an empty symbol")
            if not (0.0 < w <= 1.0):
                raise ValueError(f"target weight for {sym} must be in (0, 1], got {w}")
        total = sum(self.target_weights.values())
        if total > 1.0 + 1e-9:
            raise ValueError(f"rebalance target_weights sum to {total:.4f} (> 1.0)")
        return self

    def universe(self) -> tuple[str, ...]:
        """Sorted symbol tuple the rebalancer trades: targets plus the defensive asset."""
        syms = set(self.target_weights)
        if self.defensive_symbol:
            syms.add(self.defensive_symbol)
        return tuple(sorted(syms))

    def max_single_weight(self) -> float:
        """Largest weight any single asset could be asked to hold (incl. regime rotation).

        Used to lift the gate's per-symbol cap to fit the configured basket — otherwise a
        target above ``max_position_pct`` (e.g. 60% SPY vs a 20% single-stock cap) would be
        silently clamped, leaving the book permanently under-invested. Accounts for the
        worst-case regime de-risk that rotates equity weight into the defensive symbol.
        """
        weights = dict(self.target_weights)
        if self.regime_derisk_enabled and self.regime_derisk_shift > 0.0:
            freed = 0.0
            for symbol in self.equity_symbols:
                weight = weights.get(symbol)
                if weight is None:
                    continue
                reduced = weight * (1.0 - self.regime_derisk_shift)
                freed += weight - reduced
                weights[symbol] = reduced
            if freed > 0.0 and self.defensive_symbol:
                weights[self.defensive_symbol] = weights.get(self.defensive_symbol, 0.0) + freed
        return max(weights.values()) if weights else 0.0


class YoloConfig(BaseModel):
    """Max-risk 'YOLO' sleeve knobs (env-prefixed ``PAPERHANDS_YOLO__``).

    The honest *contrast* line: instead of matching SPY with less risk, this sleeve chases
    momentum + crowd hype, holding a concentrated basket of the hottest names. It is paper-only
    and never promoted to live — its job is to *show on the same axes* how much wilder hype-
    chasing is than the rebalancer or SPY.

    Slice 1 ships a point-in-time-safe **price/volume hype proxy** (no look-ahead): the score
    blends rate-of-change, a volume spike vs trailing average, and breakout proximity. Real
    social fields (``social_score``, ``trump_mention``, ``reddit_mentions``) flow through
    ``HypeContext`` and are blended in via ``social_weight`` once a feed is wired — until then
    they are null and contribute nothing.

    Like the rebalancer, this emits target-weight orders the sovereign gate sizes; the gate
    stays the final authority (it never exceeds cash, caps position count, rejects malformed
    output). ``apply_mode_requirements`` lifts the gate's caps to fit this sleeve's concentration.
    """

    model_config = {"frozen": True}

    # How many of the hottest names to hold (concentration: fewer = riskier).
    top_n: int = Field(default=5, gt=0)
    # Per-symbol cap the gate enforces (cranked up vs the conservative default — this is YOLO).
    max_position_pct: float = Field(default=0.5, gt=0.0, le=1.0)

    # Hype-score blend weights (applied to clamped, non-negative ``SignalSet`` components;
    # the indicator *windows* are owned by ``SignalConfig``, not duplicated here).
    momentum_weight: float = Field(default=1.0, ge=0.0)  # SignalSet.roc
    volume_weight: float = Field(default=1.0, ge=0.0)  # SignalSet.volume_spike (vs trailing avg)
    breakout_weight: float = Field(default=0.5, ge=0.0)  # SignalSet.dist_from_high (chase highs)
    social_weight: float = Field(default=0.0, ge=0.0)  # SignalSet.social_score (null until fed)

    # Weight the top-N by hype score (conviction) vs equal-weight them.
    conviction_weighted: bool = True

    # Optional blowup guard: full-exit a held name down more than this from its avg cost
    # (None = diamond hands, ride it to zero). Default off — YOLO.
    stop_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)


class BrokerConfig(BaseModel):
    """Simulated-broker settings: starting capital + the execution cost model.

    Env-prefixed ``PAPERHANDS_BROKER__``. Costs are config, never hardcoded. Slippage and
    spread are in basis points; half the spread is applied to each side of a fill.
    """

    model_config = {"frozen": True}

    starting_cash: float = Field(default=100_000.0, gt=0.0)
    slippage_bps: float = Field(default=5.0, ge=0.0)
    spread_bps: float = Field(default=2.0, ge=0.0)  # full spread; half applied per side
    commission_per_order: float = Field(default=0.0, ge=0.0)

    # Liquidity-aware costs (default off): extra spread that scales inversely with a name's
    # average dollar volume, so illiquid small-caps pay realistically more. Capped.
    liquidity_cost_enabled: bool = False
    liquidity_impact_coef: float = Field(default=0.0, ge=0.0)  # bps per $1M of ADV (inverse)
    liquidity_max_extra_bps: float = Field(default=0.0, ge=0.0)  # cap on the extra spread


class EngineConfig(BaseModel):
    """Backtest-engine settings (env-prefixed ``PAPERHANDS_ENGINE__``).

    ``calendar_symbol`` defines the trading calendar (and pre-stages the §9 benchmark);
    ``rebalance_every_n_days`` controls decision cadence (1 = every trading day);
    ``adv_window`` is the lookback for the gate's average-dollar-volume liquidity input.
    """

    model_config = {"frozen": True}

    calendar_symbol: str = "SPY"
    rebalance_every_n_days: int = Field(default=1, gt=0)
    adv_window: int = Field(default=20, gt=0)

    # Skip the screener and treat the run's universe directly as the candidate set. Used by
    # the ETF rebalancer (the universe is the fixed target basket, so there is nothing to
    # rank). The screen module stays intact and importable for other uses.
    screen_bypass: bool = False


class RecordConfig(BaseModel):
    """Result-persistence settings (env-prefixed ``PAPERHANDS_RECORD__``)."""

    model_config = {"frozen": True}

    db_path: str = "results.sqlite"


class ExecConfig(BaseModel):
    """Alpaca execution settings (env-prefixed ``PAPERHANDS_EXECUTION__``).

    The base URLs select the paper vs. live REST endpoint; ``Settings.live_trading`` (guarded
    by ``LIVE_CONFIRM``) chooses between them. ``time_in_force`` and ``fractional`` shape every
    submitted order — fractional shares let small target weights translate to honest sizes.
    """

    model_config = {"frozen": True}

    paper_base_url: str = "https://paper-api.alpaca.markets"
    live_base_url: str = "https://api.alpaca.markets"
    time_in_force: str = "day"
    fractional: bool = True


class ScheduleConfig(BaseModel):
    """Scheduler cadence (env-prefixed ``PAPERHANDS_SCHEDULE__``).

    ``runner.schedule`` runs one cycle per trading session, gated by Alpaca's market clock.
    ``open_offset_minutes`` delays the run past the open so first-minute data settles;
    ``poll_seconds`` bounds how long the loop sleeps between clock checks while it waits.
    """

    model_config = {"frozen": True}

    open_offset_minutes: int = Field(default=5, ge=0)
    poll_seconds: int = Field(default=300, gt=0)


class Settings(BaseSettings):
    """Top-level config. Env vars are prefixed ``PAPERHANDS_``; nested groups use ``__``.

    Example: ``PAPERHANDS_RISK__MAX_POSITION_PCT=0.2`` sets ``settings.risk.max_position_pct``.
    Secret keys (Tiingo, Anthropic, Alpaca) are read from their conventional env names without
    the prefix and are optional in this slice.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPERHANDS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    strategy_mode: StrategyMode = "rules-only"

    risk: RiskParams = Field(default_factory=RiskParams)
    data: DataConfig = Field(default_factory=DataConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    social: SocialConfig = Field(default_factory=SocialConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    signals: SignalConfig = Field(default_factory=SignalConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    rebalance: RebalanceConfig = Field(default_factory=RebalanceConfig)
    yolo: YoloConfig = Field(default_factory=YoloConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    record: RecordConfig = Field(default_factory=RecordConfig)
    execution: ExecConfig = Field(default_factory=ExecConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    # Secrets — optional here; required by later slices. Read from their standard env names.
    tiingo_api_key: str | None = Field(default=None, alias="TIINGO_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    alpaca_api_key: str | None = Field(default=None, alias="ALPACA_API_KEY")
    alpaca_secret_key: str | None = Field(default=None, alias="ALPACA_SECRET_KEY")

    # Live-trading guard: ``live_trading`` selects the live endpoint, but is only honored when
    # ``live_confirm`` (env ``LIVE_CONFIRM``) equals ``"I_UNDERSTAND"``. Paper is the default.
    live_trading: bool = Field(default=False, alias="LIVE_TRADING")
    live_confirm: str | None = Field(default=None, alias="LIVE_CONFIRM")


def load_settings() -> Settings:
    """Construct Settings from the environment / .env file."""
    return Settings()


def apply_mode_requirements(settings: Settings) -> Settings:
    """Coerce the plumbing flags the target-weight sleeves require, so ``--mode X`` just works.

    Both the ETF rebalancer and the YOLO sleeve are only correct with target-weight gate sizing
    and the screen bypassed (the basket / hottest-N *is* the universe). Those flags live in
    ``RiskParams`` / ``EngineConfig`` and a ``--mode`` override applied via ``model_copy`` skips
    validators, so a CLI run could otherwise size their orders through the legacy ``new-dollars``
    path. This is the single place that reconciles them; called at every composition root.
    Idempotent and a no-op for the legacy modes.

    ``needed_cap`` is the largest weight any single name may be asked to hold (the basket's max
    for rebalance, the per-name concentration cap for YOLO); ``needed_positions`` is the most
    concurrent positions the sleeve can hold (the basket size, or YOLO's ``top_n``). The gate
    stays sovereign — this only *widens* its caps to fit a deliberately-configured sleeve, never
    relaxes the hard invariants (cash, malformed-output rejection, daily loss limit).
    """
    mode = settings.strategy_mode
    if mode == "rebalance":
        needed_cap = settings.rebalance.max_single_weight()
        needed_positions = len(settings.rebalance.universe())
    elif mode == "yolo":
        needed_cap = settings.yolo.max_position_pct
        needed_positions = settings.yolo.top_n
    else:
        return settings

    updates: dict[str, object] = {}
    risk_update: dict[str, object] = {}
    if settings.risk.sizing != "target-weight":
        risk_update["sizing"] = "target-weight"
    if settings.risk.max_position_pct < needed_cap:
        risk_update["max_position_pct"] = needed_cap
    if settings.risk.max_positions < needed_positions:
        risk_update["max_positions"] = needed_positions
    if risk_update:
        updates["risk"] = settings.risk.model_copy(update=risk_update)
    if not settings.engine.screen_bypass:
        updates["engine"] = settings.engine.model_copy(update={"screen_bypass": True})
    return settings.model_copy(update=updates) if updates else settings
