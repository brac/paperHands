# PaperHands — Project Bible

A self-benchmarking, honest trading system. A pure strategy module proposes a portfolio plan, a sovereign deterministic risk gate sizes and vets it, and the result is evaluated against SPY. The strategy and risk logic are pure, swappable modules so the *same code* runs first in a historical backtest, then against a live paper account, then (only after proven) live. Live trading is gated behind an explicit config flag AND a defensible backtest.

The strategy core is now a **hands-off ETF rebalancer** (target-weight basket, drift/schedule trigger), after the original LLM-assisted technical alpha engine was tested and found to have no retail-reachable edge — see *Status* below. The goal is to match SPY with less risk, not to beat it.

This document is the source of truth. Decisions locked here should not be relitigated mid-build. Open questions live in their own section.

---

## Status — Pivot to a hands-off rebalancer (2026)

**The alpha hypothesis was tested and disproven, and that is a successful outcome of the backtest gate.** We ran broad-screen technical momentum / mean-reversion through the survivorship-aware harness across multiple regimes:
- Large-cap universe: ≈ 0 edge vs SPY.
- Small-cap universe: negative under honest (liquidity-aware) costs.

We accept that result. That strategy class has no retail-reachable edge, and we are **not** going to keep hunting for it. The disproven alpha logic is preserved, kept runnable, under `legacy/` (see `legacy/README.md`) — the honest record of what we tried.

The project pivots to its defensible fallback: a **low-turnover, hands-off ETF rebalancer** (`strategy/rebalance.py`). It holds a fixed, config-driven basket of ETFs at target weights and trades only on drift-beyond-band or a schedule. The honest goal is now **to roughly match SPY with shallower drawdowns — not to beat it.** The owner checks in occasionally (a read-only dashboard), may tweak weights/thresholds/regime params, and validates *every* change through the existing backtest harness across regimes before it touches live money. Everything below this section is read in that light; the historical decisions are kept (marked *superseded*) rather than deleted.

---

## North Star

Build an honest, measurable trading experiment — not a money-maker. The system is designed to *answer* a question with evidence before risking a cent, not to assume an answer.

**Original hypothesis (disproven):** that a disciplined, backtestable technical rule set — process edge over information edge — could find a retail-reachable edge over SPY. The harness answered no (see *Status* above). Keeping this stated plainly is the point: the system's job was to tell us the truth, and it did.

**Current goal:** risk-adjusted parity with SPY — match its return with less risk (shallower drawdowns, lower vol) via a disciplined, unemotional, low-maintenance rebalancer. We do not claim or imply outperformance. Promotion of any config change is judged on **risk-adjusted** behavior (drawdown, vol, Sharpe-ish), not raw return, and only when it improves across bull / drawdown / chop windows — never a single-window outlier.

Sequencing doctrine (unchanged, and vindicated): **prove it on history first, then build the plumbing.** A point-in-time backtest replays years over identical logic in seconds; it is what answers "is this worth running?" before the live loop answers "do the API calls fire?" That gate is exactly what stopped us going live on disproven alpha.

---

## Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python | Best ecosystem for data pipelines and backtesting |
| Build sequence | Backtest harness FIRST, Alpaca paper loop second | Edge is the open question; plumbing is the known quantity |
| Broker (live/paper, later) | Alpaca | Real retail API, free paper sandbox, commission-free, fractional shares |
| Historical data | Paid provider behind a swappable interface; **Tiingo** default (cheap, long EOD history), Polygon if paying up for deeper delisted coverage | Data quality is the #1 lever for a trustworthy backtest; cheapness here corrupts results |
| Backtest engine | Hand-rolled event-driven loop behind a thin `Engine` Protocol (no backtrader dependency in the end) | Event-driven calls strategy bar-by-bar, matching our stateful `propose_plan`; no-look-ahead is natural. |
| **Strategy core** | **Config-driven ETF rebalancer** (`strategy/rebalance.py`): target weights + drift/schedule trigger | Alpha hypothesis disproven in backtest; rebalancing is the defensible, low-maintenance fallback (match SPY with less risk) |
| **Universe** | **Fixed config ETF basket; screen bypassed** in the rebalance loop (`screen/` kept intact for later reuse) | No edge in stock selection — eliminate selection risk; the basket *is* the universe |
| **Regime use** | **De-risking overlay only** — scale equity exposure down below the 200-DMA, toggleable | Honest risk control, not an alpha/return predictor; never adds exposure |
| **Gate sizing** | **Target-weight mode** (`RiskParams.sizing`): nets buys/partial-sells to target + min-trade & max-turnover guards | True rebalance-to-target while the gate stays sovereign and identical in backtest + live |
| **Legacy alpha logic** | Archived to `legacy/`, kept runnable + tested | Reproducible honest record of what was tried and disproven |
| ~~Signal engine: technicals core, news/EDGAR veto/boost~~ | *Superseded by pivot* | Was the alpha path; no longer in the core loop (lives in `legacy/`) |
| ~~Universe: broad US equities + ETFs narrowed by a user-tunable screen~~ | *Superseded by pivot* | Replaced by the fixed ETF basket; the screen survives, bypassed |
| Historical data | **Tiingo** default behind a swappable provider interface; Polygon if paying up for deeper coverage | Data quality is the #1 lever for a trustworthy backtest |
| Broker (live/paper, later) | Alpaca | Real retail API, free paper sandbox, commission-free, fractional shares |
| Autonomy (later) | Paper-only until a live flag is flipped | Live is a config flag, not a separate system |
| Capital (live, eventual) | $100 | Hobby-scale; fractional shares mandatory; variance high |
| ~~LLM picks the plan~~ | *Superseded by pivot* | Claude-as-proposer was the alpha path (kept in `legacy/`); no LLM in the rebalance core loop |

---

## Non-Negotiable Principles

1. **Risk gate is sovereign.** The strategy proposes; the deterministic risk gate disposes. Hard rules it cannot override: max % per position, never exceed available cash, position-count cap, no illiquid/penny names, daily loss limit, reject/clamp malformed output. For the rebalancer it also owns target-weight sizing (net buys / partial sells) plus the min-trade and max-turnover churn guards — still the single, deterministic, identical-in-backtest-and-live authority.
2. **No look-ahead, ever.** Every datum used in a decision must have been available at decision time. This is the cardinal backtesting sin and the design defends against it by construction (event-driven engine + point-in-time data). The guard test stays.
3. **Backtest is a hard gate on live.** The live flag may not flip until a config's backtest is point-in-time correct and looks defensible vs SPY across multiple windows/regimes (now judged on *risk-adjusted* behavior, not raw return). **This gate already fired:** it is what prevented going live on the disproven alpha strategy. Promote a parameter change only when it improves drawdown/vol/Sharpe across regimes — never a single-window outlier.
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

For the rebalancer, stage **[2 Screen] is bypassed** (`EngineConfig.screen_bypass`): the universe is the fixed ETF basket, so there is nothing to rank. The screen module stays intact and importable for a possible later read-only "candidate surfacing" feature. Stage **[4 Propose]** is `strategy/rebalance.py` (no LLM). A new read-only **check-in dashboard** (`dashboard/`, static JSON export + a Vite/React SPA) reads the `record/` store; it reports (equity vs SPY, drift, trades + realized P&L, risk stats) and never trades.

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

- **Phase 0 — Scaffold:** repo, package skeleton, config, secrets handling, provider+broker interfaces stubbed, smoke test. *(done)*
- **Phase 1 — Backtest harness:** point-in-time backtest, user-tunable screen, real `strategy/` + `risk/` modules, simulated broker, realistic costs/slippage, SPY benchmark across windows/regimes. **Served its purpose: it disproved the broad-screen technical alpha edge** (≈0 large-cap, negative small-cap under honest costs). *(done)*
- **Phase 1b — Rebalancer pivot (THIS BUILD):** replace the alpha proposer with the config-driven ETF rebalancer (`strategy/rebalance.py`); add target-weight gate sizing + turnover/min-trade guards; bypass the screen in the core loop; archive the alpha logic to `legacy/`; add the read-only check-in dashboard. Same pure modules, same harness, no forked brain.
- **Phase 1c — The YOLO contrast sleeve (paper-only):** add a deliberately max-risk momentum/hype sleeve (`strategy/yolo.py`, `--mode yolo`) that ranks the universe by a "hype" score each cycle and piles into the hottest `top_n` names, concentrated. It exists to draw a **third line** on the dashboard next to the rebalancer and SPY — to *show* how much wilder hype-chasing is — and is **never promoted to live**. Honesty constraints (the same doctrine, applied to a fun feature): Slice 1 trades a **point-in-time price/volume hype proxy** (rate-of-change + volume spike vs trailing average + breakout proximity, all as-of-safe), so the backtest line is labeled **"YOLO (proxy hype)"** and the no-look-ahead guard still fires in yolo mode. Real exotic feeds ride the existing secondary-feed seam — a `SocialProvider` → `HypeContext` → `SignalSet`. **The Reddit-WSB feed is wired** (`ingest/wsb.py`): a one-time offline builder (`python -m ingest.wsb`) extracts ticker mentions from a raw WSB post archive into a compact, indexed `wsb_daily` SQLite aggregate, and `RedditWSBFeed` serves a point-in-time hype score (mention count + velocity, `log1p`-compressed) per symbol, summing only days `<= as_of`. Set `PAPERHANDS_SOCIAL__ENABLED=true` and raise `PAPERHANDS_YOLO__SOCIAL_WEIGHT` to let meme hype drive the buys — this is the "meme mindset" made concrete: a name's WSB mention volume accelerating *is* the buy thesis (the 2022 archive correctly lights up GME/AMC/BBBY on their squeeze days). Truth-Social / Trump posts remain a stubbed `SocialProvider` for later; a live forward line comes with the paper loop (Slice 3). The sovereign gate stays sovereign: `apply_mode_requirements` only *widens* its caps (concentration, position count) to fit the sleeve and forces target-weight sizing + screen bypass — it never relaxes the hard rules (cash, malformed-output rejection, daily loss limit), and liquidity-aware costs stay on so meme small-caps pay realistic slippage. Run both lines + export the combined dashboard JSON via `python -m runner.compare`.
- **Phase 2 — Alpaca paper loop:** swap historical feed → Alpaca live data, simulated broker → Alpaca paper. Mostly plumbing; the rebalancer brain is unchanged. Paper-only.
- **Phase 3 — Rebalance tuning:** validate weights, drift band, rebalance cadence, and the regime de-risk overlay via multi-window + walk-forward evaluation. Promote only risk-adjusted improvements (drawdown/vol/Sharpe), never single-window outliers.
- **Phase 4 — Live (opt-in):** flip the flag with $100 *only after* a config's backtest looks defensible across regimes. Success metric is **SPY-parity return at lower drawdown/vol**, not excess return.

---

## Open Questions

- **YOLO hype-score blend & concentration:** the proxy weights (momentum / volume-spike / breakout), `top_n`, and per-name cap are unvalidated knobs — the sleeve is a *contrast line*, not a promoted strategy, so it is tuned for legibility, not for a backtest gate. When a real social feed lands, re-weight `social_weight` and re-judge.
- **Real social point-in-time integrity:** the YOLO backtest line is only honest if the social archive is genuinely as-of-correct (a post's timestamp = when it was visible). A scrape that backfills edited/deleted posts would reintroduce look-ahead. Enforce via the same `ingest.guard` no-look-ahead check.
- **Rebalance cadence & drift band:** monthly schedule vs drift-triggered (±band), and the band width — to be walk-forward validated. Default shipped: drift trigger at ±5% on a SPY/BND/GLD 60/30/10 basket.
- **Regime de-risk overlay:** whether the below-200-DMA equity de-risking actually improves drawdown across regimes without dragging return too far — validate before enabling by default (ships off).
- **Defensive rotation target:** rotate de-risked equity weight into bonds (BND) vs hold as cash — config knob; pick a default after backtesting.

### Resolved by the pivot
- ~~Position sizing inside the gate (equal vs conviction-weighted)~~ → **resolved:** target-weight rebalance sizing (`RiskParams.sizing="target-weight"`), with min-trade and max-turnover guards.
- ~~How news/filing flags translate to conviction~~ → **moot:** the news/LLM path is archived in `legacy/`; not in the rebalance core.
- ~~LLM-in-the-backtest cost~~ → **moot:** the rebalancer makes no API calls; bulk multi-window backtests are cheap.
- ~~Cadence: daily vs intraday vs weekly~~ → folded into the rebalance cadence question above (the rebalancer is intentionally low-frequency).
