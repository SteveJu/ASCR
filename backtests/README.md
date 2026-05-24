# ASCR Backtests

Historical validation code for the ASCR event-driven AI supply-chain strategy.

This package is isolated from live ASCR and ASCR-H runtime state. It uses a separate SQLite database under `backtests/data/`, which is ignored by git.

## What This Backtest Tests

The research question:

> Could ASCR have discovered useful AI supply-chain opportunities from public filings and historical prices using only information available at the time?

The public code focuses on:

- SEC 8-K filings
- SEC Form 4 insider activity
- historical prices from yfinance
- LLM-based filing/event classification
- dynamic universe simulation
- sector-aware stop logic
- QQQ and SPY benchmark comparison

## Research Result Summary

These are historical research results from the private research branch. They are not a promise of future returns.

| Version | Universe | Rebalance | Gross Return | Est. Net Return | QQQ Benchmark | Sharpe | Max DD | Win Rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | Fixed 46 tickers | Daily | +170% | ~+160% | +39.3% | 2.74 | -19% | n/a |
| V2 | Dynamic universe | Weekly | +115% | ~+108% | +39.3% | 2.26 | -22% | n/a |
| V3 Weekly | Enriched events + insider signals | Weekly | +173% | ~+165% | +39.3% | 2.92 | -19% | 70% |
| V3 Daily | Enriched events + insider signals | Daily | +260% | ~+245% | +39.3% | 3.55 | -17% | 64% |
| Live Replay | Blank memory + ASCR-H-style reject rules | Daily | +217% | ~+207% | +39.3% | 3.25 | -21% | n/a |
| QQQ benchmark | Buy and hold QQQ | n/a | +39.3% | +39.3% | +39.3% | n/a | n/a | n/a |

`Live Replay` is the stricter mode and should be treated as the more realistic research baseline. It starts with no event memory, applies a one-trading-day execution lag, imports ASCR trading exclusions, and lets ASCR-H-style paper rules block orders.
Estimated net return is an approximate public-reporting haircut for commissions,
spread, and slippage. Users should rerun with their own explicit cost model.

## Public Safety

This directory intentionally excludes:

- `.env`
- SQLite databases
- downloaded SEC cache
- generated price data
- private logs

Create your own local `.env`:

```bash
cp .env.example .env
```

Required or recommended values:

```bash
GEMINI_API_KEY=<your-key>
SEC_USER_AGENT_EMAIL=<your-email>
```

Install the backtest dependencies:

```bash
pip install -r requirements.txt
```

## How It Works

```text
1. fetch_prices.py     -> download historical prices
2. fetch_sec.py        -> download SEC 8-K and Form 4 data
3. enrich_data.py      -> fetch 8-K item details and insider-derived events
4. analyze_filings.py  -> classify filings into structured events
5. simulator.py        -> V1 simulation
6. simulator_v2.py     -> dynamic universe, sector-aware simulation
7. live_replay.py      -> blank-memory ASCR/ASCR-H replay with order rejections
```

## Run

From this directory:

```bash
python3 run_backtest.py fetch
python3 run_backtest.py analyze
python3 run_backtest.py sim
python3 run_backtest.py v2
python3 run_backtest.py live
python3 run_backtest.py status
```

`live` accepts two optional environment variables:

```bash
LIVE_REPLAY_RUN_ID=example_run python3 run_backtest.py live
LIVE_REPLAY_PROFILE=sec_only python3 run_backtest.py live
BACKTEST_DB_PATH=data/other.sqlite python3 run_backtest.py live
```

Recommended source profiles:

```bash
# Formal baseline: SEC 8-K events only
LIVE_REPLAY_PROFILE=sec_only \
LIVE_REPLAY_RUN_ID=sec_only_20250513_20260512 \
python3 run_backtest.py live

# Formal expanded run: SEC + Form 4 + future parsed 13F events
LIVE_REPLAY_PROFILE=sec_form4_13f \
LIVE_REPLAY_RUN_ID=sec_form4_13f_20250513_20260512 \
python3 run_backtest.py live

# Exploratory run: formal sources plus historical news/event backfills
LIVE_REPLAY_PROFILE=news_exploratory \
LIVE_REPLAY_RUN_ID=news_exploratory_20250513_20260512 \
python3 run_backtest.py live
```

For custom experiments:

```bash
LIVE_REPLAY_EVENT_SOURCES=sec_8k_backtest,insider_backtest \
python3 run_backtest.py live
```

For the earlier one-year period:

```bash
python3 run_backtest_v2_period.py fetch
python3 run_backtest_v2_period.py enrich
python3 run_backtest_v2_period.py analyze
python3 run_backtest_v2_period.py sim
python3 run_backtest_v2_period.py status
```

## Known Limitations

- V1 has survivorship bias because the fixed universe was chosen after the theme was known.
- Historical Google News and Reddit are not included; the public backtest relies mostly on SEC filings and prices.
- Google News RSS can be explored with date-filtered searches, but it is not a stable historical archive. Treat it as exploratory unless you use a proper historical news dataset.
- 13F filings can be added more rigorously than news because they are structured SEC filings. They must be applied from filing date, not quarter-end date.
- LLM classification can drift as providers update models.
- Slippage, fees, and market impact are simplified.
- The AI infrastructure theme was unusually strong during the tested period.

The point is not to claim certainty. The point is to make the research method inspectable.
