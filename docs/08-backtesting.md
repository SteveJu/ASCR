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

## How Public Users Should Treat The Results

Use the results as a reason to inspect the methodology, not as proof of future returns.

Good questions to ask:

- Which data was knowable on the trade date?
- Did the system accidentally use future information?
- How sensitive are results to thresholds?
- What happens if rebalance frequency changes?
- What happens with a broader universe?
- Does the system survive weak AI market regimes?

## Public Backtest Package

The public repo includes:

- `backtests/run_backtest.py`
- `backtests/run_backtest_v2_period.py`
- SEC download scripts
- price download scripts
- event enrichment scripts
- simulators
- benchmark comparison
- configuration for different universe assumptions

It does not include private SQLite snapshots. Users generate their own local `backtests/data/*.sqlite` files.
