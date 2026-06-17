"""Application configuration, loaded once from env/.env and injected downstream.

Built on pydantic-settings so types are validated at load time. Pure modules never read
this directly — the composition root (`runner/`) constructs it and passes the pieces it
needs (e.g. a ``RiskParams``) into pure functions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from risk.params import RiskParams

StrategyMode = Literal["rules-only", "llm"]
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
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    signals: SignalConfig = Field(default_factory=SignalConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
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
