# Changelog

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
