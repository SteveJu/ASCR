# Backtesting

Backtesting is included to make the research falsifiable. The goal is not to claim guaranteed future returns. The goal is to show how the idea behaved under a defined historical methodology.

## Research Question

Can an event-driven AI supply chain system discover tradable signals from public filings and price history?

## Private Research Result Summary

The public `backtests/` package is a sanitized migration of the research backtest code. It uses:

- SEC 8-K filings
- SEC Form 4 insider events
- historical prices
- LLM event classification
- dynamic universe simulation
- sector-aware stops
- QQQ and SPY benchmarks

Headline results:

| Version | Universe | Rebalance | Return | Sharpe | Max Drawdown | Win Rate |
|---|---|---:|---:|---:|---:|---:|
| V1 | Fixed universe | Daily | +170% | 2.74 | -19% | n/a |
| V2 | Dynamic universe | Weekly | +115% | 2.26 | -22% | n/a |
| V3 Weekly | Enriched events + insider signals | Weekly | +173% | 2.92 | -19% | 70% |
| V3 Daily | Enriched events + insider signals | Daily | +260% | 3.55 | -17% | 64% |
| Live Replay | Blank memory + ASCR/ASCR-H style execution | Daily | +217% | 3.25 | -21% | n/a |

The Live Replay result is the stricter comparison point. It starts with no positions, no event memory, and no prior sell history. Events become visible only after an execution lag, ASCR-style ranking generates intent, and ASCR-H-style paper rules can reject orders.

## Why V1 Is Weaker Evidence

V1 used a fixed universe selected after the AI supply chain theme was already known. That introduces survivorship bias.

V2 and V3 are more useful because they move toward a dynamic universe where tickers become eligible only after appearing in historical events.

## Known Limitations

- Historical Google News and Reddit data are not fully reproducible.
- The backtest relies heavily on SEC filings and historical prices.
- LLM classification can drift by model version.
- Slippage and market impact are simplified.
- Backtest performance can overstate real-world performance.
- The AI supply chain theme was unusually strong during the tested period.

## Data Source Notes

News can be tested, but it is not as clean as SEC data. Google News RSS can return historical-looking search results with date filters, but it is not a stable historical archive. Search ranking and coverage may change, and each query is capped. Treat Google News backfills as an exploratory layer, not the primary evidence.

13F filings are more suitable for formal backtests than RSS news because they are SEC filings with filing dates and structured information tables. They must still be handled conservatively: a 13F can only affect the simulation on or after its filing date, not the quarter-end report date. It is a delayed institutional confirmation signal, not a fast catalyst.

The recommended reporting split is:

- `SEC only`
- `SEC + Form 4 + 13F`
- `SEC + Form 4 + 13F + exploratory news`

The public backtest runner exposes those as live replay profiles:

```bash
cd backtests

LIVE_REPLAY_PROFILE=sec_only \
LIVE_REPLAY_RUN_ID=sec_only_20250513_20260512 \
python3 run_backtest.py live

LIVE_REPLAY_PROFILE=sec_form4_13f \
LIVE_REPLAY_RUN_ID=sec_form4_13f_20250513_20260512 \
python3 run_backtest.py live

LIVE_REPLAY_PROFILE=news_exploratory \
LIVE_REPLAY_RUN_ID=news_exploratory_20250513_20260512 \
python3 run_backtest.py live
```

Use `LIVE_REPLAY_EVENT_SOURCES=source_a,source_b` for ad hoc source ablations.

## How Public Users Should Treat The Results

Use the results as a reason to inspect the methodology, not as proof of future returns.

Good questions to ask:

- Which data was knowable on the trade date?
- Did the system accidentally use future information?
- How sensitive are results to thresholds?
- What happens if rebalance frequency changes?
- What happens with a broader universe?
- Does the system survive weak AI market regimes?
- Does the result survive a blank-memory replay with ASCR-H rejection rules?

## Public Backtest Package

The public repo includes:

- `backtests/run_backtest.py`
- `backtests/run_backtest_v2_period.py`
- SEC download scripts
- price download scripts
- event enrichment scripts
- simulators
- live-style replay simulator
- benchmark comparison
- configuration for different universe assumptions

It does not include private SQLite snapshots. Users generate their own local `backtests/data/*.sqlite` files.
