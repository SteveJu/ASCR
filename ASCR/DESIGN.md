# ASCR V4.3 — Design Document

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
  → Python pre-filter (keyword match)
  → Haiku 4.5 filter when Python pre-filter leaves too many candidates
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
- On actionable event: triggers ASCR-H via subprocess
- Cost model: fetch is free, dedup is free, LLM only fires on genuinely new events

## 3. Ranking System

### Scoring

Each ticker accumulates an event score over a 30-day window:

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

## 8. Discovery

### Discovery Engine (`src/discovery_engine.py`)
- Broad Google News queries for AI supply chain themes
- Counterparty name→ticker resolution (30+ mappings)
- yfinance validation (real US stock, reasonable market cap)
- Human approval required for universe addition

### Sector Discovery (`src/sector_discovery.py`)
- Anomaly detection: track mention frequency changes (0→many mentions)
- LLM classifies: "Is this company genuinely connected to AI supply chain?"
- No keyword-based approach — catches "0 to 1" moments

### Universe Pruner (`src/universe_pruner.py`)
- 5 trigger types: D-tier, no events 60d, negative sentiment, sector heat death, delisted
- 2+ triggers required to remove (except delisted = immediate)
- Never removes tickers with open positions

## 9. LLM Configuration

| Task | Model | Fallback | Cost |
|------|-------|----------|------|
| Headline filter | Haiku 4.5 | Python-only if small batch | Logged in `llm_calls` |
| Event analysis | Gemini primary | Flash → Sonnet fallback | Logged in `llm_calls` |
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
- `mentions` — Social media mention tracking
- `activity_log` — Structured operation log
- `llm_calls` — AI API usage/cost log by model and purpose

`data/experience.sqlite`:

- `signal_outcomes` — Event → actual price impact tracking
- `patterns` — Discovered recurring patterns
- `monthly_reviews` — Opus review history
- `signal_type_stats` — Per event-type accuracy stats

## 11. Telegram Bot

Bot: **ASCR Bot** (@jusinvest_bot)

Full command set with Chinese/English support. Long-polling daemon (KeepAlive).

Key commands: `/picks` (top recommendations), `/why X` (deep analysis), `/ticker X` (full report), `/system` (architecture report), `/bubble` (circuit breaker status).

## 12. Weekly Health Check

`scripts/healthcheck.py` — automated via OpenClaw cron (Sundays 10:00 AM):

1. Import all modules (both projects)
2. Shadowed import detection
3. DB integrity (PRAGMA integrity_check)
4. Data freshness
5. Launchd job status
6. Recommender integration test
7. Position sanity check

Auto-fixes bugs, commits, pushes, reports to Telegram.

## 13. Known Limitations

- **Backtest survivorship bias**: Universe was selected knowing performance. Dynamic discovery in V2+ mitigates this.
- **Gemini 2.5 Flash thinking**: Can consume output token budget. Mitigated with thinkingBudget cap.
- **Reddit sentiment**: Low signal-to-noise. Weighted 0.8× vs other sources.
- **Insider events**: Ambiguous (mostly compensation sells). Weighted 0.5×.
- **No real-time price feeds**: yfinance has 15-min delay + rate limits. Sufficient for 15-min polling.
