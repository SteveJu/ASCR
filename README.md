# ASCR: AI Supply Chain Radar

**Version:** v1.9.0 frontier radar and thesis receipts

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#quick-start)
[![Status](https://img.shields.io/badge/status-research%20framework-orange)](#current-status)
[![Paper Trading](https://img.shields.io/badge/trading-paper%20only-lightgrey)](#what-it-does-not-do)
[![Local First](https://img.shields.io/badge/storage-local%20SQLite-green)](#privacy-model)

ASCR is an open-source, local-first research system for studying the AI infrastructure supply chain and adjacent frontier technologies with event-driven signals, high-risk opportunity discovery, and paper trading.

**Keywords:** AI supply chain, frontier technology, humanoid robotics, commercial space, quantum computing, event-driven investing, market intelligence, stock analysis, paper trading, quantitative finance, LLM investing research, SEC filings, backtesting, Telegram bot, SQLite.

It is designed as two separate systems:

```text
ASCR   = the brain
         watches public information, extracts events, scores tickers, ranks opportunities

ASCR-H = the hands
         reads ASCR intent, applies paper-trading constraints, simulates orders, tracks PnL
```

This repository is a public, sanitized version of a private working system. It contains source code, tests, docs, sample configs, and setup guidance. It does not contain private API keys, Telegram tokens, databases, logs, or local account state.

ASCR is not financial advice, not a broker, and not an auto-trading system. It is a research framework for people who want to inspect, fork, test, and improve an event-driven approach to AI supply-chain investing.

## Who This Is For

ASCR is for:

- builders who want a local-first market intelligence project to inspect and extend
- quant/dev researchers interested in event-driven stock signals
- people studying AI infrastructure and supply-chain equities
- LLM engineers looking for a practical structured-output finance workflow
- paper-trading users who want transparent rules instead of black-box alerts
- open-source contributors who want to challenge the backtest and improve the methodology

ASCR is not for anyone looking for guaranteed returns, live broker execution, or a finished financial product.

## What Problem It Tries To Solve

The AI buildout is not only about one GPU company. It is a supply chain:

- memory and storage
- semiconductor equipment
- optical networking
- power and cooling
- data centers
- energy and grid infrastructure
- chip design and EDA tools
- adjacent frontier domains such as humanoid robotics, robotics automation, commercial space, quantum computing, and grid infrastructure

ASCR asks:

> When a new public event happens somewhere in the AI supply chain, which public companies might benefit or be hurt before the market fully digests it?

The system turns public events into structured evidence, then ranks companies by signal strength, conviction, and risk.

## What It Does

ASCR, the brain:

- fetches public market information
- watches news, SEC filings, price events, and supply-chain signals
- maps frontier technology domains and watch-only chokepoint themes
- tracks thesis receipts so early ideas can be reviewed against later evidence
- routes headlines through a quant-style materiality score before LLM analysis
- extracts structured events with LLM assistance
- logs AI API usage and estimated cost
- scores evidence, asymmetry, momentum, risk, event alpha, valuation sanity checks, business quality, and optional feedback alpha
- ranks tickers in an AI supply-chain universe
- outputs buy/sell/hold intent for a paper executor

ASCR-H, the hands:

- reads ASCR recommendations
- validates paper trades against execution rules
- sizes simulated positions from cash and portfolio constraints
- records paper orders and positions
- tracks equity, drawdown, decisions, and outcomes
- produces reports and optional Telegram notifications

## Why It May Be Interesting

Most stock tools start from charts, factors, or broad financial statements. ASCR starts from public events:

- a customer win
- an 8-K material agreement
- an earnings call comment about AI demand
- a data center power bottleneck
- a supply shortage
- an insider cluster
- a supplier or counterparty mention

The system then asks whether that event has second-order implications elsewhere in the AI supply chain. The goal is not to let an LLM trade. The goal is to convert messy public information into inspectable structured evidence.

## What It Does Not Do

ASCR does not:

- place real broker orders
- manage real money
- guarantee returns
- hide risk behind an LLM
- require you to trust opaque decisions

The design goal is inspectable research. LLMs help extract and classify text; deterministic code makes the scoring and execution decisions.

## Architecture

```text
Public data
  ├─ Google News RSS
  ├─ SEC filings
  ├─ Yahoo Finance
  ├─ price events
  ├─ frontier domain queries
  ├─ chokepoint watchlists
  └─ optional sentiment / discovery sources

ASCR
  ├─ event pipeline
  ├─ SQLite event store
  ├─ frontier radar and thesis receipts
  ├─ scoring, calibration, ablation, and ranking
  ├─ recommendation engine
  └─ AI usage and cost logs

ASCR-H
  ├─ paper account state
  ├─ trading rule validation
  ├─ simulated orders
  ├─ positions and equity curve
  └─ decision and performance reports
```

See [docs/01-architecture.md](docs/01-architecture.md).

## Repository Layout

```text
ASCR/
  ASCR/            # signal brain: events, scoring, ranking, recommendations
  ASCR-H/          # paper-trading hands: orders, positions, PnL, constraints
  backtests/       # reproducible historical validation code
  config/          # public example configs
  docs/            # setup, architecture, API, DB/logs, backtesting notes
  examples/        # API-key-free sample outputs
  .env.example     # local secrets template
  README.md
```

Runtime files such as `.env`, `data/`, `logs/`, and SQLite databases are intentionally ignored by git.

## Backtesting Methodology Update

The public `backtests/` package now includes a stricter live-style replay mode:

```bash
cd backtests
python3 run_backtest.py live
```

This mode starts from a blank state, ingests historical events forward in time, applies an execution lag, and lets ASCR-H-style paper rules reject orders. It is intended to be a more realistic complement to the older V1/V2/V3 simulators.

See [docs/08-backtesting.md](docs/08-backtesting.md).

## News Routing Update

ASCR includes a deterministic `quant_score` front-end for news. The router is
stricter than topical relevance: it favors material contracts, guidance changes,
SEC filings, customer wins/losses, funding or dilution, supply disruption,
major counterparty signals, and explicit magnitude.

It filters low-value headline flow before LLM analysis, including generic
analyst notes, "best AI stocks" lists, vague AI hype, syndicated market recaps,
and generic 13F notices that do not name a position change.

Default knobs:

```bash
ASCR_PREFILTER_MIN_SCORE=50
ASCR_PREFILTER_MAX_PER_BATCH=12
ASCR_RELEVANT_MAX_PER_RUN=45
```

## Frontier Radar Update

ASCR v1.9.0 adds a standalone frontier radar for early discovery in domains that
may become investable before they are obvious in the core AI supply-chain
universe:

- AI infrastructure and data center bottlenecks
- humanoid robotics
- robotics and physical automation
- commercial space
- quantum computing
- frontier energy and grid infrastructure

This layer is deliberately watch-only. It can scan domain-tagged queries, score
chokepoint candidates, and preserve thesis receipts, but it does not directly
create buy/sell recommendations or paper-trading orders.

Useful commands:

```bash
cd ASCR
python3 -m src.frontier_domains --queries
python3 -m src.chokepoint --report --limit 20
python3 -m src.thesis_receipts --sync
python3 -m src.frontier_radar
python3 -m src.main frontier
```

## Fundamental Sanity Checks

ASCR is designed to discover high-risk re-rating candidates, so it is not a
traditional value screen. P/E is deliberately weak because early-cycle winners
can have temporarily depressed or noisy earnings.

The scoring engine now uses bounded overlays instead:

- valuation sanity checks: P/S, EV/Sales, EV/EBITDA, and price/free-cash-flow
  carry more weight than P/E when deciding whether an event is already priced in
- business quality: margins, free-cash-flow margin, ROE, revenue growth, and
  net debt/EBITDA help separate durable businesses from weak event beneficiaries

These overlays adjust evidence/asymmetry/risk but do not override fresh event
evidence. The intended product framing is signal radar and research trigger, not
a standalone fundamental portfolio decision engine.

## Quick Start

Clone:

```bash
git clone https://github.com/SteveJu/ASCR.git
cd ASCR
```

Create a Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r ASCR/requirements.txt
pip install -r ASCR-H/requirements.txt
pip install -r backtests/requirements.txt
```

Create local config:

```bash
cp .env.example .env
cp config/telegram.example.yaml config/telegram.yaml
cp ASCR/config/telegram.example.yaml ASCR/config/telegram.yaml
cp ASCR-H/config.yaml.example ASCR-H/config.yaml
mkdir -p ASCR/data ASCR/logs ASCR-H/data ASCR-H/logs
```

Edit `.env` with your own keys:

```bash
GEMINI_API_KEY=<your-key>
ANTHROPIC_API_KEY=<your-key>
SEC_USER_AGENT_EMAIL=<your-email>
TELEGRAM_ASCR_BOT_TOKEN=<optional-token>
TELEGRAM_ASCR_H_BOT_TOKEN=<optional-token>
TELEGRAM_CHAT_ID=<optional-chat-id>
```

Telegram is optional. You can run the core tests and local research flow without Telegram.

## Verify The Install

Run ASCR tests:

```bash
cd ASCR
python3 tests/test_regressions.py
python3 tests/test_brain_contract.py
python3 tests/test_event_schema.py
python3 tests/test_event_daemon_contract.py
python3 tests/test_scoring.py
python3 tests/test_scoring_calibration.py
python3 tests/test_scoring_ablation.py
python3 tests/test_scoring_feedback.py
python3 tests/test_chokepoint.py
python3 tests/test_frontier_domains.py
python3 tests/test_frontier_radar.py
python3 tests/test_thesis_receipts.py
```

Run ASCR-H tests:

```bash
cd ../ASCR-H
python3 tests/test_validation.py
python3 tests/test_execution.py
python3 tests/test_trading_rules.py
python3 tests/test_db_integrity.py
```

Compile all Python files:

```bash
cd ..
python3 -m py_compile $(find ASCR/src ASCR/scripts ASCR/tests ASCR-H/src ASCR-H/tests -name '*.py' -print)
```

## Running The System

The exact production workflow depends on your local config and API keys, but the intended flow is:

```text
1. initialize local SQLite databases
2. run ASCR scans to collect events and scores
3. inspect ASCR rankings and portfolio intent
4. initialize ASCR-H with paper cash
5. run ASCR-H to simulate orders from ASCR intent
6. review decisions, positions, equity, and logs
```

Useful docs:

- [Installation](docs/03-installation.md)
- [Configuration](docs/02-configuration.md)
- [Database and logs](docs/06-database-and-logs.md)
- [API keys and cost controls](docs/07-api-keys-and-costs.md)
- [Telegram setup](docs/04-telegram-setup.md)
- [Running the system](docs/05-running-the-system.md)

## Backtest Summary

The `backtests/` package tests whether public filings, historical prices, and LLM event extraction could produce useful AI supply-chain signals.

Headline research results:

| Version | Universe | Rebalance | Gross Return | Est. Net Return | QQQ Benchmark | Sharpe | Max Drawdown | Win Rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | Fixed universe | Daily | +170% | ~+160% | +39.3% | 2.74 | -19% | n/a |
| V2 | Dynamic universe | Weekly | +115% | ~+108% | +39.3% | 2.26 | -22% | n/a |
| V3 Weekly | Enriched events + insider signals | Weekly | +173% | ~+165% | +39.3% | 2.92 | -19% | 70% |
| V3 Daily | Enriched events + insider signals | Daily | +260% | ~+245% | +39.3% | 3.55 | -17% | 64% |
| Live Replay | Blank memory + ASCR-H-style execution | Daily | +217% | ~+207% | +39.3% | 3.25 | -21% | n/a |
| QQQ benchmark | Buy and hold QQQ | n/a | +39.3% | +39.3% | +39.3% | n/a | n/a | n/a |

`Live Replay` is the stricter baseline because it starts with no memory, applies an execution lag, imports ASCR trading exclusions, and lets ASCR-H-style paper rules reject orders.
Estimated net return is an approximate public-reporting haircut for commissions,
spread, and slippage. It is not a substitute for rerunning the backtest with your
own cost model.

The recommended source split is:

- `sec_only`
- `sec_form4_13f`
- `news_exploratory`

These numbers are historical research, not a prediction. The point of publishing ASCR is to make the method inspectable and easier to challenge.

See [docs/08-backtesting.md](docs/08-backtesting.md).

Backtest code is available in [backtests/](backtests/).

## Scoring Validation

ASCR includes a validated scoring stack:

- `event_alpha`: decayed, source-weighted structured events
- `feedback_alpha`: optional ASCR-H outcome feedback with sample-size shrinkage
- `scoring_calibration.py`: weight search against score/forward-return pairs
- `scoring_ablation.py`: baseline vs calibrated vs feedback walk-forward comparison

Calibration reports include a stability guard. If the top grid candidates have
effectively tied objective scores but materially different weights, the selected
profile falls back to the current baseline. The same calibration path also
evaluates event-alpha `source_weights` and `event_type_weights`; config changes
remain manual after review.

The default production weights are pinned to the strict as-of ablation winner:

```yaml
evidence: 0.35
asymmetry: 0.30
momentum: 0.25
risk: -0.10
```

To run local validation after generating your own DBs:

```bash
cd ASCR
python3 -m src.scoring_calibration
python3 -m src.scoring_ablation
```

Feedback validation only uses ASCR-H outcomes known as of each historical scoring date, avoiding future outcome leakage.

## Privacy Model

The public repo should never contain:

- real API keys
- Telegram bot tokens
- `.env`
- SQLite databases
- logs
- local account state
- private chat ids
- personal absolute paths

The AI supply-chain universe is public research content. Private notification style, local runtime data, and personal workflow overlays should stay local.

See [docs/00-public-release-checklist.md](docs/00-public-release-checklist.md).

## Current Status

Current public release: v1.9.0

- sanitized ASCR source tree
- sanitized ASCR-H source tree
- sanitized backtest source tree
- frontier radar for humanoid robotics, robotics automation, commercial space, quantum computing, frontier energy, and AI infrastructure bottlenecks
- watch-only chokepoint scoring and thesis receipt tracking
- quant-style event news router with materiality scoring and LLM budget caps
- validated scoring engine with calibration and ablation tools
- calibration stability guard and event-alpha source/type calibration reports
- Gemini 3.1 Flash Lite default for cost-controlled event analysis
- coverage-aware weekly health check with launchd failure detection
- ASCR-H quote-aware position status using live previous close when available
- live replay backtest with ASCR-H-style execution constraints
- tests for both systems
- GitHub Actions CI for compile checks and tests
- API-key-free sample outputs in `examples/`
- sample universe and config templates
- docs for setup, APIs, DB/logs, and backtesting
- MIT license
- no private runtime state or secrets

Planned improvements:

- cleaner CLI entrypoints for first-time users
- public-safe Telegram bot rewrite
- screenshots and richer report samples

## Contributing

Useful contributions include:

- improving setup docs
- testing on a fresh machine
- finding broken assumptions in the backtest
- adding public-safe data adapters
- improving risk controls
- making ASCR-H execution rules easier to audit

Please do not open issues or PRs containing real API keys, Telegram tokens, screenshots with chat ids, or private account state.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

ASCR is for research and education. It is not investment advice, not a solicitation to buy or sell securities, and not a production trading system. You are responsible for your own decisions, risk management, API costs, and compliance obligations.
