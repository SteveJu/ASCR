# ASCR v2.0

AI Supply Chain and Frontier Technology Opportunity Discovery System.

Event-driven signal detection for AI supply chain and adjacent frontier technology stocks. Scans news, SEC filings, insider trades, and social sentiment in real-time. Generates high-risk research priorities and buy/sell signal candidates via LLM-assisted analysis. Pure signal intelligence — no auto-trading.

## Architecture

```
Data Sources (free, Python-only)         LLM Analysis              Output
─────────────────────────────────       ──────────────            ────────
Google News RSS (41 queries)     ──┐    Quant pre-score
SEC 8-K (item-level parsing)     ──┤    Haiku: tie-break         Rankings
SEC 13F (6 major funds)          ──┤──▶ Gemini: analyze          ──▶ Buy/Sell signals
SEC Form 4 (insider trading)     ──┤    Sonnet: fallback         Explosive alerts
Yahoo Finance (earnings+ratings) ──┤                              ASCR-H trigger
Reddit (WSB/stocks/investing)    ──┤
Price events (surge/crash/vol)   ──┘
```

**Key principle**: Radar = brain. All analysis, ranking, buy/sell decisions happen here. ASCR-H is a pure executor with zero judgment.

## Real-Time Event Daemon

Continuous monitoring during market hours (9:00 AM - 4:30 PM ET):
- Polls every 15 minutes for new articles and SEC filings
- Hash-based dedup — only calls LLM on genuinely new items
- Quant headline scoring keeps material events first and filters low-value topical news before deep analysis
- Immediately triggers ASCR-H on actionable events
- Sends Telegram event pushes only for high-impact explosive events; routine actionable events stay logged for ranking and ASCR-H
- Cost: ~$0.03/cycle max (most cycles are free — nothing new)

## Modules

| Category | Modules |
|----------|---------|
| **Core** | `recommender` (ranking engine), `scoring`, `scoring_calibration`, `scoring_ablation`, `scoring_feedback`, `rating`, `config` |
| **Data** | `event_pipeline`, `price_fetcher`, `sec_fetcher`, `insider_tracker`, `news_fetcher` |
| **Analysis** | `analysis_engine`, `event_daemon`, `fast_scan`, `discovery_engine`, `sector_discovery`, `frontier_radar`, `frontier_domains`, `chokepoint`, `thesis_receipts` |
| **LLM** | `gemini_client` (Gemini 3.1 Flash Lite default → Flash fallback), `llm_usage` (usage/cost logging), `event_classifier`, `model_router` |
| **Risk** | `bubble_detector` (3-level circuit breaker), `market_regime`, `exit_rules`, `universe_pruner` |
| **Intelligence** | `supply_chain` (graph propagation), `leveraged_etf_monitor`, `experience_tracker` |
| **Output** | `telegram_notifier`, `report_generator`, `system_report` |
| **Infrastructure** | `db`, `activity_log`, `data_cleanup`, `utils` |

## Scoring Engine

ASCR v2.0 keeps the validated scoring stack, the quant-style news router,
and adds a patient execution policy plus high-impact alert gating:

- base dimensions: evidence, asymmetry, price confirmation, risk
- momentum/price trend is retained as a diagnostic, not a live opportunity-score contributor
- event alpha: time-decayed, source-weighted public events with confidence, verdict, conviction, and priced-in adjustment
- valuation overlay: bounded sanity check using P/S, EV/Sales, EV/EBITDA, and price/free-cash-flow; P/E is deliberately weak because ASCR targets high-risk re-rating candidates
- business-quality overlay: bounded filter for margins, cash generation, ROE, leverage, and growth quality
- feedback alpha: optional ASCR-H outcome feedback with sample-size shrinkage and hard caps
- calibration: grid search against score/forward-return pairs
- ablation: strict as-of comparison of baseline, calibrated, feedback, and combined scoring variants
- stability guard: flat, weight-dispersed calibration surfaces fall back to baseline
- event-alpha calibration: source/type weights are evaluated with the same IC objective
- event analysis defaults to Gemini 3.1 Flash Lite via `ASCR_EVENT_MODEL`
- weekly health checks distinguish latest single-ticker updates from full-universe coverage and flag launchd nonzero last exits
- quant headline scoring prioritizes materiality, surprise, magnitude, affected actor, and second-order supply-chain implications
- frontier domain discovery tracks AI infrastructure, humanoid robotics, robotics automation, commercial space, quantum computing, and frontier energy
- chokepoint scoring ranks watch-only bottleneck candidates by scarcity, dependency, concentration, validation, timing, and crowding risk
- thesis receipts preserve original discovery logic and later price outcomes for accountability
- portfolio instructions favor longer holds: trailing stops activate only after meaningful gains, fresh profitable positions are protected from rotation, and routine weak signals get grace before sell
- active Telegram event alerts are capped to explosive items with major source/type/keyword/evidence filters

Current production weights:

```yaml
evidence: 0.50
asymmetry: 0.40
momentum: 0.00  # diagnostic only
risk: -0.10
```

Regime-specific scoring overrides are pinned to the same validated profile until they have separate as-of validation.

ASCR should be read as a signal radar and research trigger, not a standalone
fundamental portfolio decision engine. Valuation and quality overlays are
sanity checks that temper event-driven signals; they are not hard filters.

Run local validation after generating your own local DBs:

```bash
python3 -m src.scoring_calibration
python3 -m src.scoring_ablation
```

Calibration reports include a stability guard: if the top grid candidates have
effectively tied objective scores but materially different weights, the selected
profile falls back to the current baseline. The same report also evaluates
event-alpha `source_weights` and `event_type_weights` with rank IC plus top/bottom
spread; config changes remain manual after review.

## News Routing

ASCR routes headlines through a deterministic `quant_score` before Gemini or
Sonnet analysis. The router keeps concrete, material events such as contracts,
guidance changes, SEC filings, customer wins/losses, funding or dilution, supply
disruptions, major counterparty events, and explicit magnitude.

Low-value items are filtered early: generic analyst notes, "best AI stocks"
articles, vague AI hype, syndicated market recaps, and generic 13F notices that
do not name a position change.

Default controls:

```bash
ASCR_PREFILTER_MIN_SCORE=50
ASCR_PREFILTER_MAX_PER_BATCH=12
ASCR_RELEVANT_MAX_PER_RUN=45
```

## Frontier Radar

`src/frontier_radar.py` gives ASCR a separate discovery surface for domains that
may emerge before they belong in the trading universe:

- AI infrastructure bottlenecks
- humanoid robotics
- robotics automation
- commercial space
- quantum computing
- frontier energy and grid infrastructure

The radar is discovery-only. It does not bypass the recommender, does not add
tickers automatically, and does not send paper-trading orders to ASCR-H.
Flagged anomalies now pass through a conservative promotion gate that can mark
them for human review or shadow tracking without entering the trading universe.

Useful local commands:

```bash
python3 -m src.frontier_domains --queries
python3 -m src.chokepoint --report --limit 20
python3 -m src.thesis_receipts --sync
python3 -m src.frontier_radar
python3 -m src.frontier_promotion
python3 -m src.frontier_promotion --sync-shadow
python3 -m src.main frontier --json
```

## Backtest Snapshot

Public headline research table, with QQQ measured over the 2025-05-13 to
2026-05-13 replay window. Estimated net return is a rough transaction-cost
haircut, not a substitute for rerunning with your own assumptions.

| Version | Gross Return | Est. Net Return | QQQ Benchmark | Max DD |
|---|---:|---:|---:|---:|
| V3 Daily | +260% | ~+245% | +39.3% | -17% |
| Live Replay | +217% | ~+207% | +39.3% | -21% |
| QQQ benchmark | +39.3% | +39.3% | +39.3% | n/a |

## Event-Driven Ranking

- Each event analyzed by LLM → verdict (STRONG_BUY/BUY/HOLD/AVOID/SELL) + conviction (HIGH/MEDIUM/LOW)
- Ranking: `verdict_score` > `ev_score` > `evidence`
- Event window: 30 days, score cap: avg × min(count, 8)
- Minimum `ev_score ≥ 5` required for recommendation
- Event source/type multipliers are reviewed through `src.scoring_calibration`
  instead of being treated as fixed intuition.
- Price confirmation can explain context but cannot make an event-thin ticker qualify by itself.

## Backtest Source Profiles

The public backtest package separates source profiles so evidence quality is explicit:

| Profile | Purpose |
|---|---|
| `sec_only` | Formal filing-only baseline |
| `sec_form4_13f` | Adds insider and delayed institutional confirmation |
| `news_exploratory` | Adds historical news-style signals for exploratory comparison |

The stricter live replay starts with blank memory and applies ASCR-H-style execution constraints. Use it to test whether ASCR intent survives realistic paper-trading rules.

## Universe

- **57 configured tickers** across 11 sectors: compute, optical, networking, memory, semicap, data_center, eda_ip, energy_grid, power_cooling, memory_storage, new_additions
- **Excluded from trading** (tracked for signal intelligence only): NVDA, GOOG, GOOGL, MSFT, AMZN, META, AAPL, TSM, AVGO
- Dynamic discovery: new tickers found via news/counterparty/SEC scanning
- Pruning: 2+ removal-eligible triggers required; D-tier needs enough scoring history, negative sentiment requires both count and ratio, and `excluded_from_trading`/`keep` names are exempt.

## Bubble Protection

3-level circuit breaker:
- ⚠️ WARNING: >80% declining + >50% drop >5%
- 🔴 DANGER: >90% declining + >60% drop >8%
- 💀 MELTDOWN: >95% declining + >80% drop >10% → auto-liquidate all positions

## Schedule (Mon-Fri, launchd)

| Time | Job | Purpose |
|------|-----|---------|
| 07:00 | `com.ascr.daily` (AM) | Prices + scores |
| 09:00-16:30 | `com.ASCR.event-daemon` | Continuous event monitoring (15min cycles) |
| 12:30 | `com.ASCR.fast-scan` | 8-K urgent + breaking news |
| 16:30 | `com.ASCR.discovery` | New ticker discovery |
| 17:15 | `com.ASCR.universe-scan` | Weekly universe evaluation |
| 17:30 | `com.ascr.daily` (PM) | Full event pipeline + rankings + Telegram push |
| Optional | private overlay | Interactive Telegram layer, not included in the public repo |

## Telegram

Telegram notification support is optional. The first public ASCR source drop does not include the private interactive command bot.

## Data

- **DB**: `data/ascr.sqlite` (events, prices, scores, tickers, `thesis_receipts`, and `llm_calls` usage/cost log)
- **Experience**: `data/experience.sqlite` (signal accuracy tracking)
- **Leveraged ETF**: `data/leveraged_etf_state.json`

## Examples

The repository-level `examples/` directory contains API-key-free sample outputs:

- `scoring_result_sample.json`
- `event_pipeline_output_sample.json`
- `backtest_equity_curve_sample.csv`

## AI API Usage & Cost

All successful Gemini and Anthropic calls are logged to `llm_calls` with model, purpose,
input/output tokens, and estimated USD cost.

```bash
sqlite3 data/ascr.sqlite \
"select model, purpose, count(*), sum(input_tokens), sum(output_tokens), round(sum(cost_usd), 4)
 from llm_calls group by model, purpose order by sum(cost_usd) desc;"
```

Expected monthly cost is still low single digits in normal operation because most cycles
stop at RSS fetch + Python dedup. The cost log is the source of truth for actual spend.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
# Optional: cp config/telegram.example.yaml config/telegram.yaml
python3 -m src.main run-daily
```

## Quality Gate

Repository CI runs `compileall` plus ASCR and ASCR-H tests on every push and pull
request.
