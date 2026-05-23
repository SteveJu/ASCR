# ASCR: AI Supply Chain Radar

**Version:** v1.0.0 public shell

ASCR is an open-source event-driven research and paper-trading system for the AI infrastructure supply chain.

The project has two parts:

```text
ASCR   = the brain: AI Supply Chain Radar
ASCR-H = the hands: AI Supply Chain Radar Hands
```

ASCR watches public information, turns events into structured signals, ranks opportunities, and explains why a stock may deserve attention. ASCR-H reads ASCR's intent, applies execution constraints, simulates trades, records decisions, and reports performance.

This is not a broker, not financial advice, and not an auto-trading system. It is a research framework for people who want to study event-driven investing with their own API keys, Telegram bots, and paper account assumptions.

## Why This Exists

The AI buildout is not one stock. It is a supply chain:

- chips and memory
- semiconductor equipment
- optical networking
- power and cooling
- data centers
- energy and grid infrastructure
- software and design tools

ASCR tries to answer one practical question:

> When a new event happens somewhere in the AI supply chain, which public companies may benefit or be hurt before the market fully digests it?

## Core Ideas

- **Event-driven, not price-only.** Price action matters, but the first-class input is new information.
- **LLMs extract judgment; rules make decisions.** LLMs classify and summarize. Deterministic scoring and trading rules decide what the system outputs.
- **Brain and hands are separate.** ASCR produces buy/sell/hold intent. ASCR-H handles sizing, constraints, simulated fills, PnL, and logs.
- **Everything should be inspectable.** Signals, costs, decisions, trades, and outcomes should be stored locally and explainable.

## Repository Layout

```text
ASCR/
  ASCR/           # Brain: events, scoring, ranking, recommendations
  ASCR-H/         # Hands: paper execution, positions, orders, PnL, constraints
  backtests/      # Historical validation tools and summarized results
  config/         # Example configs only
  docs/           # Setup, architecture, strategy notes, privacy checklist
  examples/       # Sample outputs and payloads
```

The public version intentionally ships without private runtime state. The AI supply chain universe is public research content and can be shared. You bring your own API keys, Telegram bots, and local runtime data.

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/ASCR.git
cd ASCR

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
cp config/telegram.example.yaml config/telegram.yaml
cp config/universe.sample.yaml config/universe.yaml
```

Then edit `.env` and `config/telegram.yaml` with your own credentials.

See [docs/03-installation.md](docs/03-installation.md) for the full setup.

Detailed setup guides:

- [Configuration](docs/02-configuration.md)
- [Telegram setup](docs/04-telegram-setup.md)
- [Database and logs](docs/06-database-and-logs.md)
- [API keys and cost controls](docs/07-api-keys-and-costs.md)

## What You Need

- Python 3.11+
- A Gemini API key, Anthropic API key, or both
- Optional Telegram bot token and chat id
- Internet access for public data sources such as Yahoo Finance, Google News RSS, and SEC EDGAR

## Backtest Summary

The private research backtest used SEC filings, historical prices, and LLM event analysis to test whether the event-driven framework had signal value.

Headline result from the research branch:

| Version | Universe | Rebalance | Return | Sharpe | Max Drawdown | Win Rate |
|---|---|---:|---:|---:|---:|---:|
| V1 | Fixed universe | Daily | +170% | 2.74 | -19% | n/a |
| V2 | Dynamic universe | Weekly | +115% | 2.26 | -22% | n/a |
| V3 Weekly | Enriched events + insider signals | Weekly | +173% | 2.92 | -19% | 70% |
| V3 Daily | Enriched events + insider signals | Daily | +260% | 3.55 | -17% | 64% |

Important caveat: these are research results, not a promise of future returns. The public repo should make the methodology reproducible and inspectable rather than asking anyone to trust a headline number.

See [docs/08-backtesting.md](docs/08-backtesting.md).

## Privacy And Safety

This repo should never contain:

- real API keys
- Telegram bot tokens
- `.env`
- local SQLite databases
- logs
- local account state
- private chat ids
- personal file paths

Chinese-localized bot messages and personal notification workflows are private by default. They can be added later as a generic localization layer, but they should not be mixed into the first public release.

See [docs/00-public-release-checklist.md](docs/00-public-release-checklist.md) before publishing.

## Status

This public package is being assembled from a private working system. The first public milestone is:

- sanitized configuration
- reproducible setup
- ASCR and ASCR-H code without private paths
- tests
- backtest methodology and summarized results
- documentation good enough for another person to run the system with their own keys

## Disclaimer

ASCR is for research and education. It is not investment advice, not a solicitation to buy or sell securities, and not a production trading system. You are responsible for your own decisions, risk management, API costs, and compliance obligations.
