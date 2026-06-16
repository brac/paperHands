# PaperHands — Phase 1 Spec: Live Paper-Trading Loop

**Goal of this phase:** Stand up the full 7-stage pipeline running end-to-end against an Alpaca **paper** account on a schedule, with complete logging and correct SPY benchmarking. The strategy logic may be deliberately simple. The *deliverable is the plumbing and the sovereign risk gate*, not alpha.

**Out of scope for Phase 1:** the backtest harness (Phase 2), sophisticated signals, live trading. The live flag must remain off and the code path for live must be a single config switch, not implemented behavior we rely on yet.

This spec is written for Opus to decompose into detailed implementation plans. Each section below is a buildable unit with explicit acceptance criteria. Build order respects dependencies.

---

## Definition of Done (phase-level)

- A single command runs one full cycle against the paper account and exits cleanly.
- A scheduler can run that cycle on a cadence (default: once daily, on a US market trading day, after open).
- Every cycle produces a persisted, inspectable record: inputs, raw LLM plan, gated plan, orders, fills, portfolio snapshot, and SPY-relative benchmark.
- The risk gate has thorough unit tests and provably cannot pass an unsafe plan.
- `strategy/propose_plan(...)` is a pure function: same inputs → same outputs, no side effects, no I/O. (LLM call is injected as a dependency so it can be stubbed.)
- Secrets (API keys) are never committed; loaded from env/.env.
- A `LIVE_TRADING=false` default; flipping it is the *only* difference between paper and live execution paths.

---

## 0. Scaffold & Config (dependency for everything)

Build the repo skeleton matching the module boundaries in `PROJECT.md` (`ingest/ screen/ signals/ strategy/ risk/ execute/ record/ runner/`), plus `tests/`.

- Config object loaded from env/.env: Alpaca keys, base URL (paper), Anthropic key, universe params, risk params, cadence, `LIVE_TRADING` flag (default false).
- Dependency-injection-friendly: clients (broker, LLM) constructed once and passed down, never imported ad hoc inside pure modules.
- Logging set up (structured, leveled).

**Acceptance:** `python -m runner.smoke` authenticates to Alpaca paper, prints account equity and buying power, and exits 0.

---

## 1. Ingest

Clients + a point-in-time snapshot assembler.

- **Broker/market data (Alpaca):** fetch daily bars for the universe, latest quotes, current positions, cash, buying power.
- **Secondary feeds (stub-friendly interfaces, minimal real impl in P1):** Alpaca news endpoint; SEC EDGAR client (recent 8-K / Form 4 by ticker); FRED client for a couple of macro series. In P1 these can return real data but the *integration depth* is shallow — the point is the interface exists and feeds the snapshot.
- Assemble a single immutable `MarketSnapshot` dataclass with a timestamp, keyed by ticker, carrying prices/indicateable history + attached news/filing flags + macro context + account state.

**Acceptance:** given a small universe, produces a populated `MarketSnapshot` with no network calls leaking into downstream modules. No look-ahead: snapshot timestamp is explicit and all data predates it.

---

## 2. Screen

Reduce the broad universe to a candidate set the LLM can reason over.

- Input: full liquid universe (define how the universe list is sourced — e.g. a static seed list of liquid equities + major ETFs for P1; document the path to a dynamic source later).
- Filter for liquidity (min avg dollar volume) and rank by a simple composite (e.g. momentum + relevance). Keep top N (config, e.g. 20).
- Pure function over the snapshot.

**Acceptance:** returns ≤ N candidates from a larger universe deterministically; unit-tested on a fixture snapshot.

---

## 3. Signals

Compute technical indicators on candidates. Pure functions over price frames.

- A starter indicator set: trend (e.g. moving-average relationships), momentum (e.g. rate of change / RSI-like), volatility (e.g. ATR-like), and a mean-reversion measure. Keep it modest but real.
- Attach the secondary news/filing flags from the snapshot as structured booleans/scores per candidate (e.g. `recent_insider_buy`, `recent_8k`, `news_sentiment`).
- Output: a structured `SignalSet` per candidate — numeric, JSON-serializable.

**Acceptance:** indicators match hand-computed values on a fixture; no NaN leakage; output is fully serializable (it will be fed to the LLM).

---

## 4. Propose (LLM strategy module)

The swappable core. `strategy/propose_plan(signals, positions, cash, llm_client) -> ProposedPlan`.

- Pure except for the injected `llm_client` (so tests stub it).
- Builds a prompt that: states the technicals-core / news-secondary doctrine explicitly; provides the structured `SignalSet`, current positions, and available cash; and demands a **JSON-only** response matching a strict schema — an array of `{action: buy|sell|hold, symbol, target_weight_or_qty, conviction, reason}`.
- System prompt must instruct: technicals drive the decision; news/filing flags may raise or lower conviction or veto a buy, but may **not** originate a trade on their own; respond with JSON only, no prose, no markdown fences.
- Robust parsing: strip any stray fences, validate against the schema, and on any parse/validation failure return an explicit empty/`hold` plan (never crash, never guess).

**Acceptance:** with a stubbed LLM returning canned JSON, produces a valid `ProposedPlan`; with malformed LLM output, returns a safe empty plan and logs the failure. No network in unit tests.

---

## 5. Risk Gate (sovereign — highest-scrutiny unit)

Deterministic, non-LLM. Takes a `ProposedPlan` + account state, returns a safe `ExecutablePlan`.

Hard rules (all config-driven, all enforced regardless of LLM intent):
- Never allocate more than `max_position_pct` of equity to a single symbol.
- Never spend more cash than available buying power.
- Cap total number of positions at `max_positions`.
- Reject symbols failing a liquidity/price floor (no penny stocks; min price).
- Enforce a `daily_loss_limit`: if breached, the only allowed actions are closes/holds.
- Reject/clamp any malformed, NaN, negative, or out-of-bounds quantity or weight.
- Convert target weights to fractional-share order quantities safely.

**Acceptance:** exhaustive unit tests including adversarial inputs (LLM asks to spend 10x cash, 100% in one name, negative qty, unknown symbol, NaN weight, exceed daily loss limit). The gate must clamp or reject every one. A property-style test: no input produces an order set that violates any rule.

---

## 6. Execute

Order submission with the paper/live switch.

- Broker abstraction with one implementation (Alpaca) that selects paper vs live endpoint purely from the `LIVE_TRADING` flag.
- Submit the `ExecutablePlan` as fractional orders; capture order IDs and (where available) fills.
- Idempotency/safety: never submit on a malformed plan; refuse to run live unless the flag is explicitly true *and* a guard (e.g. an env confirmation) is set.

**Acceptance:** against paper, submits orders from a sample plan and returns confirmations; with `LIVE_TRADING=false`, provably cannot hit the live endpoint.

---

## 7. Record & Benchmark

Persist the full cycle and compute SPY-relative performance.

- Persistence: SQLite (or parquet) for P1. One row/record per cycle capturing: timestamp, snapshot summary, raw LLM plan, gated plan, orders, fills, portfolio equity, and the SPY benchmark value at the same timestamp.
- Benchmark: track a notional SPY-only position from the same start date/capital and report portfolio return vs SPY return over the identical window.
- A small read-side: a command that prints the latest cycle and a running portfolio-vs-SPY summary.

**Acceptance:** after N cycles, the store contains N complete records and the summary command reports portfolio vs SPY return correctly over the window.

---

## 8. Runner & Scheduler

Orchestration.

- `runner.cycle` executes stages 1–7 once, end-to-end, with structured logging at each stage boundary.
- `runner.schedule` runs `cycle` on the configured cadence, only on US market trading days, after open (handle market calendar/holidays).
- Clean failure handling: a stage failure aborts the cycle safely (no partial/unsafe execution) and logs.

**Acceptance:** `python -m runner.cycle` runs one full clean cycle against paper; the scheduler triggers cycles on the cadence and skips non-trading days.

---

## Suggested Build Order (dependency-respecting)

0 Scaffold → 5 Risk Gate (build the safety core early, test-first) → 1 Ingest → 2 Screen → 3 Signals → 4 Propose → 6 Execute → 7 Record → 8 Runner.

Building the risk gate second (right after scaffold, before anything can execute) means the sovereign safety layer exists and is tested before any code path can place an order.

---

## Cross-Cutting Requirements

- Type hints throughout; dataclasses for the data contracts (`MarketSnapshot`, `SignalSet`, `ProposedPlan`, `ExecutablePlan`).
- Pure modules stay pure (no I/O in `screen/ signals/ strategy/propose_plan/ risk/`).
- Unit tests for every pure module; the risk gate gets adversarial + property tests.
- Secrets via env only. `.env.example` committed, `.env` git-ignored.
- No look-ahead anywhere; every datum is timestamped ≤ snapshot time.
- `LIVE_TRADING` defaults false and is the single switch separating paper from live.
