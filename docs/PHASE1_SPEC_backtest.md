# PaperHands — Phase 1 Spec: Backtest Harness

**Goal of this phase:** Stand up a point-in-time-correct backtest that runs the real `strategy/` + `risk/` modules over historical data via an event-driven engine and a simulated broker, with a user-tunable screen, realistic costs, full logging, and SPY benchmarking across multiple windows. **This phase answers the only question that matters before plumbing: does the strategy have defensible historical edge?**

**Out of scope for Phase 1:** Alpaca integration, live/paper trading, the live flag's execution path. Those are Phase 2 and must not be needed to complete this phase.

Written for Opus to decompose into detailed implementation plans. Each section is a buildable unit with explicit acceptance criteria. Build order respects dependencies.

---

## Definition of Done (phase-level)

- A single command runs a full backtest over a configurable date range + universe and outputs a performance report vs SPY.
- The backtest is **point-in-time correct**: at every simulated step, only data available at-or-before that step is visible to the strategy. Demonstrated by a look-ahead guard test.
- The user-tunable screen works: changing config (sectors, cap floor, liquidity, watchlist, ranking weights) measurably changes the candidate set.
- The same `strategy/propose_plan` and `risk/` modules that will later run live are the ones exercised here — no backtest-only fork of the brain.
- Realistic costs (slippage + spread; commission≈0) are applied to every simulated fill.
- The risk gate has exhaustive adversarial + property tests and provably cannot pass an unsafe plan.
- Results persist; a report command shows portfolio vs SPY return, plus basic stats (CAGR, max drawdown, Sharpe-ish, hit rate) across multiple windows/regimes.
- A `rules-only` strategy mode exists for cheap bulk runs; `llm` mode is selectable for shorter windows. Swapping modes requires no change to engine/risk code.

---

## 0. Scaffold & Config (dependency for everything)

Repo skeleton matching `PROJECT.md` module boundaries (`data/ ingest/ screen/ signals/ strategy/ risk/ broker/ engine/ record/ runner/`) + `tests/`.

- Config from env/.env + a run-config file: data-provider keys, date range, universe seed, screen knobs, risk params, cost model params, strategy mode (`rules-only`|`llm`), Anthropic key (for llm mode).
- Dependency injection: data provider, broker, and LLM client constructed once and passed down; never imported inside pure modules.
- Structured leveled logging.

**Acceptance:** `python -m runner.smoke` loads config, instantiates the chosen data provider, fetches one symbol's history for a date range, and exits 0.

---

## 1. Data Provider Layer

Provider interface + at least one paid implementation; point-in-time correct.

- `DataProvider` interface: daily bars for a symbol/date-range, with explicit as-of semantics.
- Default impl: **Tiingo** (EOD). Optional: Polygon. Provider is swappable via config.
- Local caching of fetched history (parquet/SQLite) so repeated backtests don't re-hit the API.
- Document the survivorship-bias limitation explicitly in the module; where the provider offers delisted data, support including it.

**Acceptance:** fetches and caches a multi-year daily history for a set of symbols; a second run reads from cache without network; bars are timestamped and as-of-correct.

---

## 2. Risk Gate (sovereign — build early, test-first)

Deterministic, non-LLM. `ProposedPlan` + account state → safe `ExecutablePlan`.

Hard rules (all config-driven):
- Never exceed `max_position_pct` of equity in one symbol.
- Never exceed available cash/buying power.
- Cap positions at `max_positions`.
- Enforce min price / liquidity floor (no penny stocks).
- Enforce `daily_loss_limit`: if breached, only closes/holds allowed.
- Reject/clamp malformed, NaN, negative, or out-of-bounds qty/weight.
- Convert target weights → fractional-share quantities safely.

**Acceptance:** exhaustive unit tests incl. adversarial inputs (spend 10x cash, 100% one name, negative qty, unknown symbol, NaN weight, breached loss limit). Property test: no input yields an order set violating any rule.

---

## 3. Ingest & MarketSnapshot

Assemble an immutable point-in-time snapshot from the active feed (historical, here).

- Given an as-of date, build a `MarketSnapshot`: per-symbol price history up to as-of, attached news/filing flags (EDGAR; shallow integration OK in P1), macro context (FRED), and simulated account state from the broker.
- Hard guarantee: nothing in the snapshot postdates the as-of timestamp.

**Acceptance:** snapshot for a given as-of date contains only ≤ as-of data; a deliberate look-ahead attempt fails a guard test.

---

## 4. Screen (user-tunable)

Reduce broad universe → candidate set per the user's config.

- Layered filter: liquidity (min avg dollar volume), min price, sector include/exclude, market-cap floor, optional pinned watchlist; then rank by configurable composite (momentum/relevance weights); keep top N.
- Pure function over snapshot + screen config.

**Acceptance:** deterministic ≤ N candidates; changing each knob measurably changes output; unit-tested on a fixture.

---

## 5. Signals

Technical indicators on candidates; pure functions over price frames.

- Starter set: trend (MA relationships), momentum (ROC/RSI-like), volatility (ATR-like), mean-reversion. Modest but real.
- Attach structured news/filing flags per candidate (`recent_insider_buy`, `recent_8k`, `news_sentiment`).
- Output: JSON-serializable `SignalSet` per candidate.

**Acceptance:** indicators match hand-computed fixture values; no NaN leakage; fully serializable.

---

## 6. Strategy: propose_plan (swappable, dual-mode)

`strategy/propose_plan(signals, positions, cash, strategy_ctx) -> ProposedPlan`. Pure except injected deps.

- **rules-only mode:** deterministic logic over signals (e.g. momentum + mean-reversion rules, news flags adjust conviction/veto). No network. For bulk historical sweeps.
- **llm mode:** builds a JSON-only prompt (technicals-core / news-secondary doctrine stated explicitly; signals + positions + cash provided; strict schema: array of `{action, symbol, target_weight, conviction, reason}`), calls injected LLM client, robustly parses (strip fences, validate, on failure return safe empty/hold plan).
- News may boost/lower conviction or veto a buy; it may **not** originate a trade alone (enforced in both modes).
- Mode selected by config; engine and risk code unchanged either way.

**Acceptance:** rules-only produces valid plans deterministically on fixtures; llm mode with a stubbed client produces valid plans and returns a safe empty plan on malformed output. No network in unit tests.

---

## 7. Simulated Broker

Execution target for the backtest.

- `Broker` interface (shared with future `AlpacaBroker`): submit orders, report positions/cash/equity.
- `SimulatedBroker`: applies fills at next-bar open (no same-bar look-ahead), with a cost model — configurable slippage (bps), spread assumption, commission≈0. Supports fractional shares.
- Tracks portfolio equity over time for reporting.

**Acceptance:** given a plan at step T, fills occur at T+1 open with costs applied; equity curve is reconstructable; no same-bar fill leakage.

---

## 8. Engine (backtrader adapter)

Drive the cycle over history via an event-driven library behind a thin adapter.

- Adapter wraps **backtrader** (default), feeding historical bars and invoking the pipeline (ingest→screen→signals→propose→risk→execute→record) once per simulated step, as-of-correct.
- The adapter isolates the library so `strategy/` and `risk/` stay library-agnostic (could swap engine without touching the brain).

**Acceptance:** runs a multi-year daily backtest end-to-end on the simulated broker, calling the real strategy + risk modules each step, with no look-ahead.

---

## 9. Record & Benchmark

Persist results; compute SPY-relative performance.

- Persistence (SQLite/parquet): per-step records (snapshot summary, raw plan, gated plan, simulated fills, equity) + a run summary.
- Benchmark: buy-and-hold SPY over the identical window/capital; report portfolio vs SPY.
- Stats: total/annualized return, max drawdown, volatility, Sharpe-ish, hit rate, turnover.
- Report command: prints the run summary and portfolio-vs-SPY across the configured windows/regimes.

**Acceptance:** after a run, the store holds complete per-step records; the report shows portfolio vs SPY plus stats across multiple windows.

---

## 10. Runner & Multi-Window Evaluation

Orchestration + the actual "does it have edge?" answer.

- `runner.backtest` executes one run for a given config (range, universe, screen, mode).
- `runner.evaluate` runs across multiple predefined windows/regimes (e.g. a bull stretch, a drawdown, a chop period) and aggregates portfolio-vs-SPY, so edge isn't a single-window fluke.
- Clean failure handling; a stage failure aborts the run safely and logs.

**Acceptance:** `python -m runner.backtest --config X` produces a report; `runner.evaluate` produces a multi-window comparison table vs SPY.

---

## Suggested Build Order (dependency-respecting)

0 Scaffold → 2 Risk Gate (safety core, test-first) → 1 Data Provider → 3 Ingest → 4 Screen → 5 Signals → 6 Strategy (rules-only first, llm after) → 7 Simulated Broker → 8 Engine → 9 Record → 10 Runner/Eval.

Risk gate second so the sovereign safety layer is tested before any execution path exists. Strategy starts in rules-only mode so the full loop is provable cheaply before adding LLM cost.

---

## Cross-Cutting Requirements

- Type hints throughout; dataclasses for contracts (`MarketSnapshot`, `SignalSet`, `ProposedPlan`, `ExecutablePlan`).
- Pure modules stay pure (no I/O in `screen/ signals/ strategy/propose_plan/ risk/`).
- Unit tests for every pure module; risk gate gets adversarial + property tests; a dedicated **no-look-ahead guard test** at the engine/ingest boundary.
- Realistic cost model applied to every fill; costs are config, not hardcoded.
- Data provider + backtest engine each sit behind a thin interface (swappable).
- Same `strategy/` + `risk/` modules must be the ones reused unchanged in Phase 2 (live). No brain fork.
- Secrets via env only; `.env.example` committed, `.env` git-ignored.
