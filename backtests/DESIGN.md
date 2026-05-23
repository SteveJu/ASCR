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
    Simulator (V1/V2/V3)
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

## 6. Database Schema (backtest.sqlite)

| Table | Rows | Description |
|-------|------|-------------|
| filings | 696 | SEC 8-K filings with optional item enrichment |
| events | 490 | LLM-analyzed events (262 SEC + 228 insider) |
| prices | 12,550 | Daily OHLCV bars |
| backtest_trades | ~150 | Simulated buy/sell orders |
| backtest_daily | 251 | Daily portfolio snapshots |
