# PaperHands — Project Bible

A self-benchmarking algorithmic trading system. An LLM-assisted strategy engine reasons over technicals (core) with news/filings as a secondary veto/boost signal, proposes a portfolio plan, passes it through a hard-coded risk gate, and is evaluated against SPY. The strategy and risk logic are pure, swappable modules so the *same code* runs first in a historical backtest, then against a live paper account, then (only after proven) live. Live trading is gated behind an explicit config flag AND a defensible backtest.

This document is the source of truth. Decisions locked here should not be relitigated mid-build. Open questions live in their own section.

---

## North Star

Build an honest, measurable trading experiment — not a money-maker. The system is designed to *answer* "does this strategy have edge?" before risking a cent, not to assume it.

Guiding bias: **process edge over information edge.** We will not win the speed/information race against institutions. What we can control is a disciplined, unemotional, backtestable rule set executed consistently. The LLM is a reasoning layer over structured signals, not an oracle reading headlines.

Sequencing doctrine: **prove edge on history first, then build the plumbing.** A forward paper loop validates plumbing but gathers a meaningful sample only over months; a point-in-time backtest replays years over identical logic in seconds. So the thing that answers "is this worth running?" is built before the thing that answers "do the API calls fire?"

---

## Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python | Best ecosystem for data pipelines and backtesting |
| Build sequence | Backtest harness FIRST, Alpaca paper loop second | Edge is the open question; plumbing is the known quantity |
| Broker (live/paper, later) | Alpaca | Real retail API, free paper sandbox, commission-free, fractional shares |
| Historical data | Paid provider behind a swappable interface; **Tiingo** default (cheap, long EOD history), Polygon if paying up for deeper delisted coverage | Data quality is the #1 lever for a trustworthy backtest; cheapness here corrupts results |
| Backtest engine | Event-driven library (**backtrader** default), behind a thin adapter | Event-driven calls strategy bar-by-bar, matching our stateful `propose_plan`; no-look-ahead is natural. (vectorbt is faster but vectorized, fighting our design.) |
| Signal engine | Hybrid: technicals core, news/EDGAR as veto/boost | Technicals are backtestable; news adjusts conviction but never originates a trade |
| Universe | Broad US equities + ETFs, narrowed by a **user-tunable screen** | User keeps control over what's eligible; engine only reasons over survivors |
| Autonomy (later) | Paper-only until a live flag is flipped | Live is a config flag, not a separate system |
| Capital (live, eventual) | $100 | Hobby-scale; fractional shares mandatory; variance high |
| LLM | Claude API, JSON-only structured output, injected dependency | Strategy proposal layer; constrained, parseable, swappable, stubbable in tests |

---

## Non-Negotiable Principles

1. **Risk gate is sovereign.** The LLM proposes; the deterministic risk gate disposes. Hard rules it cannot override: max % per position, never exceed available cash, position-count cap, no illiquid/penny names, daily loss limit, reject/clamp malformed output.
2. **No look-ahead, ever.** Every datum used in a decision must have been available at decision time. This is the cardinal backtesting sin and the design defends against it by construction (event-driven engine + point-in-time data).
3. **Backtest is a hard gate on live.** The live flag may not flip until the backtest exists, is point-in-time correct, and shows defensible edge vs SPY across multiple windows/regimes. Even then, edge may not hold — that's understood.
4. **Strategy + risk are pure, swappable modules.** `propose_plan` takes data in and returns a plan out, no side effects (LLM client injected). The risk gate is deterministic. This is what lets the identical code run in backtest, paper, and live.
5. **Provider/engine independence.** Data source and backtest engine sit behind thin interfaces; neither choice locks us in.
6. **Everything is logged; benchmark or it didn't happen.** Every cycle records inputs, raw LLM plan, gated plan, orders, fills, and the SPY-relative result. Performance is always reported relative to SPY over the identical window.

---

## High-Level Architecture

A pipeline whose pure core is reused across backtest and live:

```
[1 Ingest] -> [2 Screen] -> [3 Signal] -> [4 Propose (LLM)] -> [5 Risk Gate] -> [6 Execute] -> [7 Record]
                                                                                       |
                                          backtest: historical feed + simulated broker |
                                          live:     Alpaca feed     + Alpaca broker ----+
```

The **only** things that differ between backtest and live are the bottom layer: the data feed (historical vs Alpaca live) and the execution target (simulated broker vs Alpaca paper/live). Stages 2–5 are identical, pure code.

### Module boundaries (Python packages)
- `data/` — provider interface + implementations (Tiingo/Polygon historical; Alpaca live later). Point-in-time correct.
- `ingest/` — assembles an immutable point-in-time `MarketSnapshot` from whatever feed is active
- `screen/` — **user-tunable** universe filter/ranking (config-driven knobs)
- `signals/` — indicator computation (pure functions over price frames)
- `strategy/` — the swappable `propose_plan` module + LLM client contract
- `risk/` — the sovereign risk gate (pure, deterministic, heavily tested)
- `broker/` — execution interface: `SimulatedBroker` (backtest) + `AlpacaBroker` (later)
- `engine/` — backtest harness adapter (wraps backtrader), drives the cycle over history
- `record/` — logging, persistence, SPY benchmark computation
- `runner/` — orchestration (backtest run; later, live scheduler)

---

## The User-Tunable Screen (your control point)

The `screen/` stage is config, not a fixed heuristic. You own what's eligible:
- Base universe: liquid US equities + major ETFs (sourced list; document the path to a dynamic/delisted-aware source).
- Knobs: sector include/exclude, market-cap floor, min average dollar volume (liquidity), min price (penny-stock floor), an optional pinned watchlist, and ranking weights (e.g. momentum vs relevance).
- The engine reasons only over what survives your screen. Broad market in, your-controlled candidate set out.

---

## Data Sources (free vs paid)

**Paid / chosen:** Historical bars — Tiingo (default) or Polygon (deeper delisted coverage), behind a provider interface.
**Free / supporting:** SEC EDGAR (8-K, Form 4 insider, 13F); FRED (macro context); Alpaca news (later, live phase).
**Known limitation:** true survivorship-bias-free universe handling is hard at hobby scale; documented and partially mitigated (provider choice) rather than fully solved.
**Out of scope:** alternative data (satellite, card spend) — institutional-scale; noted only so the category is known.

---

## Risk & Honesty Caveats (read before flipping the live flag)

- Most professional managers don't beat the S&P over time. We very likely won't.
- Backtested edge famously evaporates live (overfitting, regime change, costs, slippage). A naive backtest is *worse* than none — it manufactures false confidence. Hence the strictness on point-in-time data and realistic costs.
- At $100, variance dominates; a single name can swing the account double digits on noise.
- News is usually priced in by the time it's public; hence news = secondary signal only.
- This is not investment advice; the author is not a financial advisor. Real capital is risked only after deliberate opt-in behind the flag.

---

## Phasing

- **Phase 0 — Scaffold:** repo, package skeleton, config, secrets handling, provider+broker interfaces stubbed, smoke test.
- **Phase 1 — Backtest harness (THIS BUILD):** point-in-time backtest over historical data, user-tunable screen, real `strategy/` + `risk/` modules, simulated broker, realistic costs/slippage, SPY benchmark across multiple windows/regimes. Detailed in `PHASE1_SPEC_backtest.md`.
- **Phase 2 — Alpaca paper loop:** swap historical feed → Alpaca live data, simulated broker → Alpaca paper. Mostly plumbing; the brain is already proven. (Reference draft preserved.)
- **Phase 3 — Strategy iteration:** improve signals, tune screen, deepen news/filings, walk-forward validation.
- **Phase 4 — Live (opt-in):** flip the flag with $100; compare live vs paper vs backtest.

---

## Open Questions

- **Cadence:** daily vs intraday vs weekly — *undecided.* Daily is the simplest honest default for the backtest and keeps LLM-call budget trivial; recommendation pending.
- Realistic cost model: commission (≈0 at Alpaca) + slippage assumption + spread — pick defaults for the sim broker.
- Persistence: SQLite/parquet for results — likely sufficient.
- Position sizing inside the gate: equal-weight vs conviction-weighted (clamped).
- How news/filing flags numerically translate to a conviction boost/veto.
- LLM-in-the-backtest cost: replaying years of daily cycles = many API calls; consider a cheaper/cached or rules-only mode for bulk backtests, LLM mode for shorter windows.
