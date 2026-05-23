# ASCR v1.3

AI Supply Chain Stock Opportunity Discovery & Position Exit System.

Event-driven signal detection for AI supply chain stocks. Scans news, SEC filings, insider trades, and social sentiment in real-time. Generates buy/sell recommendations via LLM analysis. Pure signal intelligence — no auto-trading.

## Architecture

```
Data Sources (free, Python-only)         LLM Analysis              Output
─────────────────────────────────       ──────────────            ────────
Google News RSS (41 queries)     ──┐
SEC 8-K (item-level parsing)     ──┤    Haiku: filter            Rankings
SEC 13F (6 major funds)          ──┤──▶ Gemini: analyze          ──▶ Buy/Sell signals
SEC Form 4 (insider trading)     ──┤    Sonnet: fallback         Telegram alerts
Yahoo Finance (earnings+ratings) ──┤                              ASCR-H trigger
Reddit (WSB/stocks/investing)    ──┤
Price events (surge/crash/vol)   ──┘
```

**Key principle**: Radar = brain. All analysis, ranking, buy/sell decisions happen here. ASCR-H is a pure executor with zero judgment.

## Real-Time Event Daemon

Continuous monitoring during market hours (9:00 AM - 4:30 PM ET):
- Polls every 15 minutes for new articles and SEC filings
- Hash-based dedup — only calls LLM on genuinely new items
- Immediately triggers ASCR-H on actionable events
- Cost: ~$0.03/cycle max (most cycles are free — nothing new)

## Modules

| Category | Modules |
|----------|---------|
| **Core** | `recommender` (ranking engine), `scoring`, `scoring_calibration`, `scoring_ablation`, `scoring_feedback`, `rating`, `config` |
| **Data** | `event_pipeline`, `price_fetcher`, `sec_fetcher`, `insider_tracker`, `news_fetcher` |
| **Analysis** | `analysis_engine`, `event_daemon`, `fast_scan`, `discovery_engine`, `sector_discovery` |
| **LLM** | `gemini_client` (Gemini primary → Flash fallback), `llm_usage` (usage/cost logging), `event_classifier`, `model_router` |
| **Risk** | `bubble_detector` (3-level circuit breaker), `market_regime`, `exit_rules`, `universe_pruner` |
| **Intelligence** | `supply_chain` (graph propagation), `leveraged_etf_monitor`, `experience_tracker` |
| **Output** | `telegram_notifier`, `report_generator`, `system_report` |
| **Infrastructure** | `db`, `activity_log`, `data_cleanup`, `utils` |

## Scoring Engine

ASCR v1.3 uses a validated scoring stack:

- base dimensions: evidence, asymmetry, momentum, risk
- event alpha: time-decayed, source-weighted public events with confidence, verdict, conviction, and priced-in adjustment
- feedback alpha: optional ASCR-H outcome feedback with sample-size shrinkage and hard caps
- calibration: grid search against score/forward-return pairs
- ablation: strict as-of comparison of baseline, calibrated, feedback, and combined scoring variants

Current production weights:

```yaml
evidence: 0.35
asymmetry: 0.30
momentum: 0.25
risk: -0.10
```

Regime-specific scoring overrides are pinned to the same validated profile until they have separate as-of validation.

Run local validation after generating your own local DBs:

```bash
python3 -m src.scoring_calibration
python3 -m src.scoring_ablation
```

## Event-Driven Ranking

- Each event analyzed by LLM → verdict (STRONG_BUY/BUY/HOLD/AVOID/SELL) + conviction (HIGH/MEDIUM/LOW)
- Ranking: `verdict_score` > `ev_score` > `evidence`
- Event window: 30 days, score cap: avg × min(count, 8)
- Minimum `ev_score ≥ 5` required for recommendation
- Event-type multipliers: SEC contract 1.2× > earnings 1.1× > news 1.0× > Reddit 0.8×

## Universe

- **57 configured tickers** across 11 sectors: compute, optical, networking, memory, semicap, data_center, eda_ip, energy_grid, power_cooling, memory_storage, new_additions
- **Excluded from trading** (tracked for signal intelligence only): NVDA, GOOG, GOOGL, MSFT, AMZN, META, AAPL, TSM, AVGO
- Dynamic discovery: new tickers found via news/counterparty/SEC scanning
- Pruning: 2+ triggers required to remove (D-tier, no events, negative sentiment, delisted)

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

- **DB**: `data/ascr.sqlite` (events, prices, scores, tickers, and `llm_calls` usage/cost log)
- **Experience**: `data/experience.sqlite` (signal accuracy tracking)
- **Leveraged ETF**: `data/leveraged_etf_state.json`

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
