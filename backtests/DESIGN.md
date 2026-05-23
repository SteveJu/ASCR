# ASCR Backtest — Design Document

> **Version**: 3.0 | **Date**: 2026-05-14

## 1. Purpose

Validate the event-driven AI supply chain trading strategy using historical SEC filings. Answer: "Would this system have made money over the past year?"

## 2. Data Pipeline

```
SEC EDGAR (EFTS API)
    │
    ├─ 8-K filings (696 total, 338 enriched)
    │   Items: 1.01 (material contracts), 2.02 (results),
    │          7.01 (regulation FD), 8.01 (other events)
    │
    ├─ Form 4 insider activity → 228 clustered events
    │
    └─ Historical prices (yfinance, 12,550 bars)
         │
         ▼
    Gemini 2.5 Flash Lite analysis
         │
         ▼
    490 events with evidence_delta, verdict, conviction
         │
         ▼
    Simulator (V1/V2/V3 + Live Replay)
```

## 3. Simulator Versions

### V1 (simulator.py)
- Fixed 46-ticker universe (survivorship bias)
- Basic hard/trailing stops
- Smart rotation
- Result: +170%, Sharpe 2.74

### V2 (simulator_v2.py)
- Dynamic universe (starts empty, tickers appear via events)
- Sector-aware stops (TICKER_SECTOR mapping)
- Weekly rebalance
- Cooldown/daily limits/sector concentration caps
- Result: +115%, Sharpe 2.26

### V3-Weekly
- Enriched data (item-level 8-K details)
- Insider signal events at 0.5x weight
- Weekly rebalance
- Result: +173%, Sharpe 2.92

### V3-Daily
- Same data as V3-Weekly
- Daily rebalance (event-speed execution)
- Result: +260%, Sharpe 3.55, DD -16.7%, WR 64%

### Live Replay (live_replay.py)
- Starts from a blank state: no positions, no event memory, no cooldown history
- Events are ingested chronologically and become tradable after a one-trading-day lag
- Uses ASCR-style ranking and ASCR-H-style paper constraints
- Applies trading exclusions for mega-cap signal sources that should not be bought
- Records blocked orders separately from executed trades
- Supports source profiles: `sec_only`, `sec_form4_13f`, and `news_exploratory`
- Result on the private research snapshot: +217%, Sharpe 3.25, DD -20.9%

This is the preferred realism baseline because it answers a stricter question: "What would the ASCR brain and ASCR-H hands have done if they started with no memory and replayed the period forward?"

## 4. Sector-Aware Stops

| Category | Hard Stop | Trailing Stop | Applies To |
|----------|-----------|---------------|------------|
| Sentiment / new | -15% | -20% | new_additions sector |
| Standard | -20% | -25% | most sectors |
| Infrastructure | -22% | -28% | power_cooling, energy_grid, data_center |

## 5. Key Findings

- **Daily >> Weekly**: +87% improvement from scanning events daily vs weekly
- **Dynamic universe eliminates survivorship bias**: V2 starts empty, more realistic
- **Enriched 8-K data matters**: +58% improvement from item-level filing details
- **Insider signals are noisy**: 0.5x weight is appropriate
- **energy_grid sector**: consistently worst (0% WR) — consider excluding
- **compute sector**: weak (38% WR, -$975) despite high trade count
- **Crisis resilience**: -16.7% max DD during US-Iran crisis, stop-losses fired correctly
- **Execution realism matters**: blank-memory live replay reduced the headline V3 daily result because execution lag, trading exclusions, cooldowns, and turnover caps blocked trades that the simpler simulator allowed
- **News is useful but hard to prove**: Google News RSS date filters can provide exploratory historical coverage, but the feed is not a stable historical archive
- **13F is a better formal add-on**: 13F data is delayed, but structured and auditable; use filing date as the availability date

## 6. Database Schema (backtest.sqlite)

| Table | Rows | Description |
|-------|------|-------------|
| filings | 696 | SEC 8-K filings with optional item enrichment |
| events | 490 | LLM-analyzed events (262 SEC + 228 insider) |
| prices | 12,550 | Daily OHLCV bars |
| backtest_trades | ~150 | Simulated buy/sell orders |
| backtest_daily | 251 | Daily portfolio snapshots |
| live_replay_trades | variable | Executed live replay orders |
| live_replay_blocked | variable | Orders rejected by paper-trading rules |
| live_replay_daily | 251/run | Live replay daily equity snapshots |

## 7. Bias Controls Added After V3

The stricter replay mode addresses several sources of overstatement:

| Bias / Issue | Control |
|---|---|
| Same-day filing execution | One-trading-day execution lag |
| Prior live memory | Start with empty event memory |
| Trading excluded mega-caps | Import ASCR trading exclusions |
| Excessive turnover | ASCR-H-style daily turnover cap |
| Rapid rebuy churn | Sell cooldown |
| Repeated same-day trading | Daily trade limit and PDT approximation |
| Result overwrites | `run_id`-scoped live replay tables |
| Source mixing | Profile-scoped event source filters |

Remaining gaps:

- 8-K analysis should use primary filing text, not only titles/items.
- Form 4 should parse XML transaction codes instead of treating all activity as unknown.
- 13F holdings should be parsed from information tables and applied only after filing date.
- Formal news backtests should use a historical news provider or be reported separately as exploratory.
