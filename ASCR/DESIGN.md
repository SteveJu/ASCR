# ASCR v1.9 — Public Design Document

## 1. Philosophy

**Event-driven, not momentum.** Price action is just another event type. We buy on information asymmetry — discovering supply chain implications before the market prices them in.

**Radar = brain, ASCR-H = hands.** All analysis, ranking, and buy/sell decisions live in ASCR's `recommender.py`. The paper trader calls `get_portfolio_instructions()` and executes blindly.

**LLM for judgment, rules for decisions.** LLMs extract, summarize, and classify. They never decide buy/sell. All output is structured JSON. Rule-based scoring turns LLM output into actionable signals.

## 2. Data Pipeline

### Sources (7, all free)

1. **Google News RSS** — 41 sector-specific queries, ~400 articles/run
2. **SEC 8-K** — Item-level parsing (1.01 contracts, 2.02 earnings, 7.01 FD disclosure, 8.01 other)
3. **SEC 13F** — 6 major fund holdings (quarterly)
4. **SEC Form 4** — Insider buying/selling patterns
5. **Yahoo Finance** — Earnings surprises, analyst rating changes
6. **Reddit** — r/wallstreetbets, r/stocks, r/investing sentiment
7. **Price events** — Surge (>5%), crash (>-5%), volume spike (>3× avg)

### Processing Chain

```
Fetch (Python, free)
  → Quant headline pre-score (materiality, surprise, magnitude, actor, noise)
  → Haiku 4.5 filter only as a tie-break when too many candidates remain
  → Gemini deep analysis (structured output; Flash fallback)
    Output: {ticker, verdict, conviction, evidence_delta, thesis, moat, catalyst, bull_case, bear_case}
  → Store to SQLite (INSERT OR IGNORE, hash dedup)
  → Log API usage/cost to llm_calls
  → Supply chain propagation (NVDA event → 30 related tickers, 30-50% attenuation)
```

### Continuous Mode (Event Daemon)

- `src/event_daemon.py` — runs as launchd KeepAlive daemon
- Active 9:00 AM - 4:30 PM ET, Mon-Fri
- 15-minute poll cycles
- Article-level hash cache (`data/seen_articles_YYYY-MM-DD.txt`) prevents re-processing
- Quant scoring ranks news by materiality before LLM calls:
  contracts, guidance, SEC filings, customer changes, funding/dilution, supply disruption, and major counterparty signals
- Generic analyst notes, best-stock lists, vague AI hype, syndicated recaps, and generic 13F notices are filtered before deep analysis
- On actionable event: triggers ASCR-H via subprocess
- Cost model: fetch is free, dedup is free, LLM only fires on genuinely new events

## 3. Ranking And Scoring System

### Scoring

ASCR v1.9 uses the validated scoring stack and adds a separate
watch-only frontier radar:

1. Base factor context: evidence, asymmetry, momentum, risk.
2. Event alpha: fresh structured events adjust evidence/asymmetry/risk using source quality, event type, confidence, verdict, conviction, novelty, decay, and priced-in discount.
3. Valuation overlay: P/S, EV/Sales, EV/EBITDA, and price/free-cash-flow temper signals where the event looks already priced in. P/E is intentionally weak because ASCR targets high-risk re-rating candidates.
4. Business-quality overlay: margins, cash generation, ROE, leverage, and growth quality distinguish durable businesses from weak event beneficiaries.
5. Feedback alpha: optional ASCR-H outcome feedback adds small bounded adjustments with sample-size shrinkage.

The calibration stability guard remains active: if the top grid candidates have
effectively tied objective scores but materially different weights, the selected
profile falls back to the current baseline. Event-alpha source and event-type
weights are evaluated through the same IC/spread calibration path. Event analysis
defaults to `gemini-3.1-flash-lite`, overridable with `ASCR_EVENT_MODEL`.

Operational reporting remains active: weekly health checks report latest
versus full-universe coverage for prices and scores, flag stale full coverage,
and treat launchd nonzero last exits as failures instead of silently reporting
loaded jobs as healthy.

v1.9 adds domain-general discovery for frontier technologies. The new frontier
radar maps domain queries, tracks chokepoint candidates, and records thesis
receipts, but it does not directly generate buy/sell recommendations.

Current public production weights:

```yaml
evidence: 0.35
asymmetry: 0.30
momentum: 0.25
risk: -0.10
```

Regime-specific overrides are pinned to this validated profile until separate regime validation exists.

The recommender still uses event quality to rank near-term opportunities:

```
ev_score = AVG(evidence_delta) × MIN(event_count, 8)
verdict_score = avg_verdict × avg_conviction
```

Where verdict maps: STRONG_BUY=3, BUY=2, HOLD=0, AVOID=-2, SELL=-3
And conviction maps: HIGH=3, MEDIUM=2, LOW=1

### Ranking Order

1. `verdict_score` (LLM judgment quality)
2. `ev_score` (event evidence strength)
3. `evidence` (raw evidence count)

### Filters

- Must have `ev_score ≥ 5` to be recommended
- Tickers in `excluded_from_trading` list are never recommended for buying
- Universe intersection enforced (no ghost tickers)

## 4. Sell Logic (5 layers)

1. **Hard stop** — P&L below sector-specific threshold (default -20%)
2. **Trailing stop** — Drop from peak exceeds sector threshold (default -25%)
3. **Thesis break** — Negative events + high heat → original thesis invalidated
4. **Universe pruner** — D-tier rating, no events 60d, negative sentiment, delisted
5. **Smart rotation** — Only sell weakest held if candidate scores >50% better

Sector-aware stops: sentiment stocks get tighter stops (-15%/-18%), infrastructure gets looser (-22%/-28%).

## 5. Supply Chain Graph

`src/supply_chain.py` — Relationship map centered on NVDA, AMD, TSM, AVGO, INTC + hyperscalers.

When NVDA files an 8-K about a new contract → event auto-propagates to ~30 related supply chain tickers with 30-50% score attenuation.

## 6. Bubble Detection

3-level circuit breaker in `src/bubble_detector.py`:

| Level | Criteria | Action |
|-------|----------|--------|
| ⚠️ WARNING | >80% declining, >50% drop >5% | Alert only |
| 🔴 DANGER | >90% declining, >60% drop >8% | Reduce position sizes |
| 💀 MELTDOWN | >95% declining, >80% drop >10% | Auto-liquidate everything |

Auxiliary: Leveraged ETF monitor tracks single-stock ETF launches and AUM as bubble/sentiment signals.

## 7. Experience Tracker

`src/experience_tracker.py` — Structured post-hoc learning:

1. **Signal accuracy** — Track each event's predicted impact vs actual 20d/40d/60d returns
2. **Pattern library** — Discover recurring patterns (e.g., "8-K contract filings predict +12% in 20d")
3. **Monthly Opus review** — Claude Opus analyzes accumulated data, suggests weight adjustments
4. **Weight adjustments** — Per event-type multipliers calibrated by empirical win rates

**Key rule**: System suggests, human approves. No auto rule changes.

The feedback layer is bounded and validated strict as-of: each historical scoring date can only use ASCR-H outcomes whose evaluation date was already known at that time.

## 8. Discovery

### Discovery Engine (`src/discovery_engine.py`)
- Broad Google News queries for AI supply chain themes
- Counterparty name→ticker resolution (30+ mappings)
- yfinance validation (real US stock, reasonable market cap)
- Human approval required for universe addition

### Sector Discovery (`src/sector_discovery.py`)
- Anomaly detection: track mention frequency changes (0→many mentions)
- LLM classifies: "Is this company genuinely connected to an investable frontier bottleneck?"
- Domain-tagged queries cover AI infrastructure, humanoid robotics, robotics automation, commercial space, quantum computing, and frontier energy
- No keyword-based approach — catches "0 to 1" moments

### Frontier Radar (`src/frontier_radar.py`)
- Standalone status report for configured frontier domains
- Pulls discovery anomalies, domain heat, and thesis receipts into one report
- Watch-only: no automatic universe promotion, recommendations, or ASCR-H orders

### Chokepoint Watchlist (`src/chokepoint.py`)
- Scores candidate bottleneck ideas by scarcity, dependency, concentration, validation, timing, and crowding risk
- Tracks rotation phase: early, front_run, consensus, crowded, or broken
- Preserves validation catalysts and death signals for later review

### Thesis Receipts (`src/thesis_receipts.py`)
- Stores original thesis, source, domain, validation path, and death signals
- Can refresh local price outcomes from the ASCR price table
- Exists for accountability, not for automated trading

### Universe Pruner (`src/universe_pruner.py`)
- 5 trigger types: D-tier, no events 60d, negative sentiment, sector heat death, delisted
- 2+ triggers required to remove (except delisted = immediate)
- Never removes tickers with open positions

## 9. LLM Configuration

| Task | Model | Fallback | Cost |
|------|-------|----------|------|
| Headline filter | Haiku 4.5 | Python-only if small batch | Logged in `llm_calls` |
| Event analysis | Gemini 3.1 Flash Lite | Flash → Sonnet fallback | Logged in `llm_calls` |
| Event alert translation | Gemini Flash Lite | Flash | Logged in `llm_calls` |
| Weekly universe eval | Opus 4.6 | — | Logged in `llm_calls` |
| Monthly review | Opus 4.6 | — | Logged in `llm_calls` |

Gemini structured output: `response_schema` with enum constraints. Forces `bull_case[]` and `bear_case[]` for adversarial reasoning.

Usage tracking lives in `src/llm_usage.py`. It records successful Gemini and Anthropic calls
to `data/ascr.sqlite::llm_calls` with model, purpose, token counts, and estimated cost.
For Gemini, thinking tokens are counted as billable output tokens.

## 10. Database Schema

`data/ascr.sqlite`:

- `events` — All detected events with LLM analysis (verdict, conviction, thesis, moat, catalyst)
- `prices` — Daily OHLCV from yfinance
- `scores` — Daily composite scores per ticker
- `tickers` — Universe registry synced from universe.yaml
- `thesis_receipts` — Watch-only early thesis records and later price outcomes
- `mentions` — Social media mention tracking
- `activity_log` — Structured operation log
- `llm_calls` — AI API usage/cost log by model and purpose

Validation/report outputs:

- `reports/scoring_calibration/YYYY-MM-DD.json`
- `reports/scoring_ablation/YYYY-MM-DD.json`

`data/experience.sqlite`:

- `signal_outcomes` — Event → actual price impact tracking
- `patterns` — Discovered recurring patterns
- `monthly_reviews` — Opus review history
- `signal_type_stats` — Per event-type accuracy stats

## 11. Telegram

The public source includes Telegram notification helpers. The private interactive command bot is intentionally not included in the public repo. Users can add their own local command layer and route it through the same scoring, reporting, and validation APIs.

## 12. Weekly Health Check

`scripts/healthcheck.py` can be run locally or scheduled by the user:

1. Import all modules (both projects)
2. Shadowed import detection
3. DB integrity (PRAGMA integrity_check)
4. Data freshness
5. Launchd job status
6. Recommender integration test
7. Position sanity check

It reports issues; any auto-fix/commit workflow should stay local and explicit.

## 13. Known Limitations

- **Backtest survivorship bias**: Universe was selected knowing performance. Dynamic discovery in V2+ mitigates this.
- **Backtest execution bias**: Prefer the blank-memory live replay for realism because it applies execution lag and ASCR-H-style rejection rules.
- **Historical news bias**: Google News RSS is useful for exploration, but it is not a stable historical archive.
- **13F lag**: 13F events must be applied from filing date, not the reported quarter-end date.
- **Gemini 2.5 Flash thinking**: Can consume output token budget. Mitigated with thinkingBudget cap.
- **Reddit sentiment**: Low signal-to-noise. Weighted 0.8× vs other sources.
- **Insider events**: Ambiguous (mostly compensation sells). Weighted 0.5×.
- **No real-time price feeds**: yfinance has 15-min delay + rate limits. Sufficient for 15-min polling.
