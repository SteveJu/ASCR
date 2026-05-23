# Changelog

## v1.2.0 - Public Backtest Package

Added the sanitized historical backtest package:

- `backtests/run_backtest.py`
- `backtests/run_backtest_v2_period.py`
- SEC filing fetch/enrichment pipeline
- historical price fetcher
- V1 and V2 simulation engines
- public universe/scoring config
- standalone backtest dependency file

Safety work:

- excluded `.env`, SQLite databases, downloaded SEC cache, generated price data, logs, and pycache
- replaced private SEC user-agent identity with `SEC_USER_AGENT_EMAIL`
- renamed public-facing backtest docs from the old private project name to ASCR
- kept the package isolated from live ASCR and ASCR-H runtime state

Validation:

- Python backtest source compiles
- safety scans found no real token, `.env`, SQLite DB, pyc file, personal path, or private email in the public backtest package

## v1.1.0 - Sanitized Source Migration

Migrated sanitized source trees into the public repo:

- `ASCR/` for the signal brain
- `ASCR-H/` for the paper-trading hands
- source tests for both projects
- sample ASCR config
- sample ASCR-H config

Safety work:

- removed private `.env`, databases, logs, and runtime state
- removed real Telegram tokens and private spec content
- removed personal absolute paths
- replaced private SEC email with `SEC_USER_AGENT_EMAIL`
- hid private Chinese-localized Telegram workflow from the first public source drop
- renamed visible project language to ASCR / ASCR-H

Validation:

- ASCR regression, scoring, rating, exit-rule, brain-contract, event-schema, and daemon-contract tests passed
- ASCR-H validation, execution, trading-rule, and DB-integrity tests passed
- Python source compiles
- public safety scans found no real token, `.env`, SQLite DB, data/log files, personal path, or Chinese bot copy

## v1.0.0 - Public Shell

Initial public distilled version of ASCR.

Includes:

- ASCR / ASCR-H naming and architecture
- public setup documentation
- privacy release checklist
- database and logs guide
- API keys and cost-control guide
- Telegram setup guide
- sample AI supply chain universe
- example environment and Telegram config

This release did not include the sanitized private project source code. Source migration started in v1.1.0.
