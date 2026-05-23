# ASCR Public Publication Status And Maintenance Plan

Goal: publish ASCR as a usable open-source project that combines ASCR, the signal brain, and ASCR-H, the paper-trading hands, while keeping all private runtime data and credentials out of the public repository.

Current status: v1.4.0 has been published with sanitized ASCR, ASCR-H, backtests, docs, sample configs, tests, the validated scoring engine, and the live replay backtest.

## Phase 1: Public Shell

Status: complete in v1.0.0.

Deliverables:

- README
- install guide
- architecture guide
- privacy release checklist
- strategy thinking document
- backtest summary document
- example config files
- strict `.gitignore`

No private code or runtime state should be copied in this phase.

## Naming

```text
ASCR   = AI Supply Chain Radar, the brain
ASCR-H = AI Supply Chain Radar Hands, the paper-trading executor
```

## Phase 2: Sanitized ASCR Migration

Status: complete in v1.1.0, with scoring upgraded in v1.3.0.

Copy the signal-engine source into `ASCR/`, then remove or refactor:

- absolute paths such as `/Users/your-name/...`, `/home/your-name/...`, or machine-specific project paths
- real Telegram config
- runtime `data/` and `logs/`
- private SQLite files
- personal SEC User-Agent email
- private launchd references

Required refactors:

- make DB paths configurable
- make ASCR-H path configurable
- read SEC user-agent email from env
- ship a public AI supply chain universe; remove only private annotations or position state
- keep tests that validate the brain contract

Acceptance:

```bash
python3 -m py_compile ASCR/src/*.py
python3 ASCR/tests/test_brain_contract.py
python3 ASCR/tests/test_event_schema.py
```

## Phase 3: Sanitized ASCR-H Migration

Status: complete in v1.1.0. Private interactive bots and runtime account state remain out of the public repo.

Copy the paper-execution source into `ASCR-H/`, then remove or refactor:

- absolute Radar path
- local paper DB
- local logs
- private Telegram bot config
- operational reports with real account state
- personal Chinese-localized bot copy unless it is intentionally made generic and documented

Required refactors:

- `ASCR_PROJECT_DIR`
- `ASCR_DB_PATH`
- `ASCR_H_DB_PATH`
- example account config
- public-safe Telegram setup

Acceptance:

```bash
python3 ASCR-H/tests/test_validation.py
python3 ASCR-H/tests/test_execution.py
```

## Phase 4: Backtest Package

Status: complete in v1.2.0. Backtest claims remain historical research context, not promises of future returns.

Copy backtest code only after removing:

- `.env`
- SQLite snapshots
- local cache files
- any private manual notes

Public backtest should be reproducible from scripts:

```text
fetch public data
enrich events
run LLM analysis
run source-profile simulations
generate report
```

Backtest claims must be labeled as historical research and include limitations.

Current public source profiles:

- `sec_only`
- `sec_form4_13f`
- `news_exploratory`

Treat `news_exploratory` separately from formal filing-only results unless a stable historical news dataset is used.

## Phase 5: Public Repo Initialization

Status: complete. Keep using this checklist before every public push.

Only after all privacy checks pass:

```bash
cd ASCR
git init
git add .
git commit -m "Describe the public-safe change"
```

Before push:

```bash
rg -n --hidden "API_KEY|TOKEN|SECRET|PASSWORD|bot_token|chat_id|sk-|AIza|ANTHROPIC|GEMINI|TELEGRAM"
rg -n "/Users/|/home/|C:\\\\"
find . -name ".env" -o -name "*.sqlite*" -o -name "*.db"
git status --short
```

## Public Positioning

Suggested repo description:

> AI Supply Chain Radar: event-driven market intelligence and paper-trading framework for researching AI infrastructure equities.

Suggested README framing:

- open-source research framework
- local-first
- bring your own API keys
- bring your own Telegram bots
- paper trading only
- transparent tests and backtests
- not financial advice

## Things Not To Claim

Do not claim:

- guaranteed returns
- production-ready auto trading
- broker execution support
- live trading safety
- that backtest performance will repeat

Do claim:

- documented research methodology
- inspectable event pipeline
- reproducible paper trading mechanics
- clear separation of signal generation and execution
- privacy-first local configuration

## Public Universe And Localization Decision

Universe:

- Public.
- This is part of the project's research value.
- Keep ticker lists, sectors, benchmarks, and high-level comments.
- Remove personal notes, position state, and private execution context.

Chinese features:

- Private by default.
- Keep Chinese-localized Telegram messages and personal notification wording out of the first public release.
- Later option: add localization as a generic `i18n/` feature with English and Chinese resource files, after removing personal workflow language.
