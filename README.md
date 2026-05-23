# ASCR: AI Supply Chain Radar

**Version:** v1.1.0 sanitized source migration

ASCR is an open-source, local-first research system for studying the AI infrastructure supply chain with event-driven signals and paper trading.

It is designed as two separate systems:

```text
ASCR   = the brain
         watches public information, extracts events, scores tickers, ranks opportunities

ASCR-H = the hands
         reads ASCR intent, applies paper-trading constraints, simulates orders, tracks PnL
```

This repository is a public, sanitized version of a private working system. It contains source code, tests, docs, sample configs, and setup guidance. It does not contain private API keys, Telegram tokens, databases, logs, or local account state.

ASCR is not financial advice, not a broker, and not an auto-trading system. It is a research framework for people who want to inspect, fork, test, and improve an event-driven approach to AI supply-chain investing.

## What Problem It Tries To Solve

The AI buildout is not only about one GPU company. It is a supply chain:

- memory and storage
- semiconductor equipment
- optical networking
- power and cooling
- data centers
- energy and grid infrastructure
- chip design and EDA tools

ASCR asks:

> When a new public event happens somewhere in the AI supply chain, which public companies might benefit or be hurt before the market fully digests it?

The system turns public events into structured evidence, then ranks companies by signal strength, conviction, and risk.

## What It Does

ASCR, the brain:

- fetches public market information
- watches news, SEC filings, price events, and supply-chain signals
- extracts structured events with LLM assistance
- logs AI API usage and estimated cost
- scores evidence, asymmetry, momentum, and risk
- ranks tickers in an AI supply-chain universe
- outputs buy/sell/hold intent for a paper executor

ASCR-H, the hands:

- reads ASCR recommendations
- validates paper trades against execution rules
- sizes simulated positions from cash and portfolio constraints
- records paper orders and positions
- tracks equity, drawdown, decisions, and outcomes
- produces reports and optional Telegram notifications

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
  └─ optional sentiment / discovery sources

ASCR
  ├─ event pipeline
  ├─ SQLite event store
  ├─ scoring and ranking
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
  config/          # public example configs
  docs/            # setup, architecture, API, DB/logs, backtesting notes
  .env.example     # local secrets template
  README.md
```

Runtime files such as `.env`, `data/`, `logs/`, and SQLite databases are intentionally ignored by git.

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

The private research branch tested whether public filings, historical prices, and LLM event extraction could produce useful AI supply-chain signals.

Headline research results:

| Version | Universe | Rebalance | Return | Sharpe | Max Drawdown | Win Rate |
|---|---|---:|---:|---:|---:|---:|
| V1 | Fixed universe | Daily | +170% | 2.74 | -19% | n/a |
| V2 | Dynamic universe | Weekly | +115% | 2.26 | -22% | n/a |
| V3 Weekly | Enriched events + insider signals | Weekly | +173% | 2.92 | -19% | 70% |
| V3 Daily | Enriched events + insider signals | Daily | +260% | 3.55 | -17% | 64% |

These numbers are historical research, not a prediction. The point of publishing ASCR is to make the method inspectable and easier to challenge.

See [docs/08-backtesting.md](docs/08-backtesting.md).

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

Current public release:

- sanitized ASCR source tree
- sanitized ASCR-H source tree
- tests for both systems
- sample universe and config templates
- docs for setup, APIs, DB/logs, and backtesting
- no private runtime state or secrets

Planned improvements:

- cleaner CLI entrypoints for first-time users
- public-safe Telegram bot rewrite
- reproducible public backtest package
- example outputs and screenshots
- GitHub Actions test workflow

## Contributing

Useful contributions include:

- improving setup docs
- testing on a fresh machine
- finding broken assumptions in the backtest
- adding public-safe data adapters
- improving risk controls
- making ASCR-H execution rules easier to audit

Please do not open issues or PRs containing real API keys, Telegram tokens, screenshots with chat ids, or private account state.

## Disclaimer

ASCR is for research and education. It is not investment advice, not a solicitation to buy or sell securities, and not a production trading system. You are responsible for your own decisions, risk management, API costs, and compliance obligations.
