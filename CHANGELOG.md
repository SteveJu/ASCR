# Changelog

## v2.0.0 - Event Research Scoring And Explosive Alerts

- Removed live momentum contribution from opportunity scoring; price trend remains available as diagnostic price confirmation
- Raised event/research weights to make evidence, asymmetry, verdict, and event alpha the primary recommendation drivers
- Added patient portfolio policy: delayed trailing-stop activation, wider default trailing bands, thesis-break grace, and rotation only for aged weak holds versus stronger fresh candidates
- Added explosive event alert gating so Telegram pushes focus on high-impact SEC, earnings, contract, guidance, bankruptcy, fraud, and major-magnitude events
- Kept routine actionable events in SQLite and ASCR-H execution flow while suppressing noisy push alerts
- Updated ASCR-H public executor defaults to event-radar execution, +30% trailing activation, -30% trailing stop, 20-day rotation hold, and alpha-aware sell DQS
- Updated public docs, config examples, and tests for v2.0 behavior

Validation:

- ASCR tests: 80 passed
- ASCR-H tests: 26 passed
- Python source compile checks passed
- Public safety scan found no private token, private path, or private repo name in tracked public source/docs

## v1.9.0 - Frontier Radar And Thesis Receipts

- Added a watch-only frontier radar for AI infrastructure, humanoid robotics, robotics automation, commercial space, quantum computing, and frontier energy
- Added domain-tagged frontier discovery queries and prompt context for anomaly classification
- Added public chokepoint research config with scarcity, dependency, concentration, validation, timing, and crowding-risk scoring
- Added thesis receipt tracking to preserve original discovery logic, validation catalysts, death signals, and later local price outcomes
- Extended `sector_discovery` to store domain context and report recent domain heat
- Added an ASCR CLI `frontier` report command
- Updated public docs and version labels for the frontier discovery release

Safety work:

- Kept the frontier radar discovery-only and watch-only; it does not directly trigger recommendations or ASCR-H paper orders
- Removed private/source-specific attribution labels from the public chokepoint config
- Added config/docs language that keeps runtime state, local paths, account state, and secrets out of the public repo

Validation:

- ASCR tests: 68 passed
- ASCR-H tests: 25 passed
- Frontier CLI smoke test passed with temporary SQLite state
- Python source compile checks passed
- Public safety scans found no real token, private path, private attribution label, or old private project name in tracked source/docs

## v1.8.0 - Quant-Style News Routing

- Added a deterministic headline `quant_score` before deep LLM analysis
- Prioritized material, incremental events: contracts, guidance, SEC filings, customer changes, funding/dilution, supply disruptions, and major counterparty signals
- Filtered low-value news before LLM calls: generic analyst notes, best-stock lists, vague AI hype, syndicated recaps, and generic 13F notices
- Added pre-analysis caps via `ASCR_PREFILTER_MIN_SCORE`, `ASCR_PREFILTER_MAX_PER_BATCH`, and `ASCR_RELEVANT_MAX_PER_RUN`
- Passed quant score and routing reason into Gemini/Sonnet analysis context
- Updated the event daemon to use the quant router directly instead of double-filtering with the old keyword prefilter
- Added unit coverage for material news retention, generic 13F suppression, ranking, and caps

Validation:

- ASCR targeted tests passed
- Python source compile checks passed

## v1.7.0 - Operational Health And Quote Reporting

- Upgraded ASCR weekly health checks to report full-universe coverage separately from latest single-ticker updates
- Added launchd status parsing that distinguishes long-running services from loaded/idle timer jobs and flags nonzero last exits
- Fixed the universe scanner no-recommendation path by avoiding a local `send` binding that shadowed the module-level notifier
- Added ASCR-H quote provider support for current price plus previous session close
- Updated ASCR-H `status` output to calculate position and portfolio daily change from live quote previous close when available
- Fixed sanitized ASCR-H backtest/regime modules that used public env-var paths without importing `os`
- Added regression coverage for healthcheck launchd parsing, universe scanner no-recommendation notifications, and ASCR-H quote-aware status rows

Validation:

- ASCR tests: 45 passed
- ASCR-H tests: 23 passed
- Python source compile checks passed

## v1.6.0 - Reliability And Model/Calendar Polish

- Added calibration stability checks that fall back to baseline when the grid-search surface is flat and weight-dispersed
- Added event-alpha source/type calibration reports using the same IC plus top/bottom spread objective
- Replaced hardcoded 2026 market holidays with dynamically generated US market holidays in ASCR and ASCR-H
- Added retry with exponential backoff for Gemini analysis and Sonnet fallback calls
- Set event analysis to Gemini 3.1 Flash Lite by default, with `ASCR_EVENT_MODEL` override
- Added GitHub Actions CI for compile checks plus ASCR and ASCR-H tests
- Added MIT license and API-key-free sample outputs under `examples/`
- Updated public backtest tables with QQQ benchmark and estimated net-of-cost return columns

Validation:

- ASCR tests: 43 passed
- ASCR-H tests: 22 passed
- Python source compile checks passed

## v1.4.0 - Live Replay Backtest

- Aligned public design/setup docs with the v1.3 validated scoring release
- clarified that private interactive Telegram command bots are not included in the public repo
- removed stale migration-status language from database/log setup docs and publication plan
- added a live-style blank-memory backtest replay that applies execution lag, ASCR trading exclusions, and ASCR-H-style order rejection rules
- added live replay source profiles for `sec_only`, `sec_form4_13f`, and `news_exploratory` runs
- documented historical news and 13F handling boundaries for formal vs exploratory backtests

## v1.3.0 - Validated Scoring Engine

Added the public-safe scoring upgrade:

- event alpha layer for decayed, source-weighted structured events
- optional feedback alpha layer using ASCR-H outcome logs
- strict as-of feedback validation to avoid future outcome leakage
- calibration tool for scoring weights
- ablation tool comparing baseline, calibrated, feedback, and combined variants
- tests for scoring, calibration, feedback, and ablation

Production scoring weights are pinned to the strict as-of ablation winner:

- evidence `0.35`
- asymmetry `0.30`
- momentum `0.25`
- risk `-0.10`

Safety work:

- ASCR-H DB path is public-safe and configurable
- no private SQLite snapshots, reports, logs, local paths, or personal identifiers were added
- private interactive bot references and local paper-account snapshots were removed from public docs/source output

Validation:

- ASCR scoring, feedback, calibration, ablation, regression, rating, exit-rule, brain-contract, event-schema, and daemon-contract tests passed
- ASCR-H validation, execution, DB-integrity, and trading-rule tests passed
- Python scoring source compiles
- safety scans found no real token, private path, personal email, `.env`, SQLite DB, log file, or pyc file in the migrated scoring files

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
