# ASCR-H

Paper trading executor for validating ASCR signals. ASCR-H is the hands; ASCR is the brain.

The production path is intentionally simple:

```text
ASCR recommender
  -> portfolio instructions: buys / sells / holds
ASCR-H
  -> validate real-world trading constraints
  -> size orders from available cash and target allocation
  -> execute simulated market orders
  -> persist orders, positions, equity, decisions
  -> send Telegram updates
```

ASCR-H should not invent buy or sell opinions in the main path. It executes Radar instructions subject to account and trading rules.

## Current Production Path

Primary entrypoint:

```bash
python3 -m src.main run-daily
```

Main modules:

| Area | Modules |
|---|---|
| Execution | `event_trader`, `intraday_monitor`, `trading_rules` |
| State | `db`, `portfolio`, `config` |
| Validation | `decision_logger`, `outcome_evaluator`, `decision_quality`, `strategy_health`, `degradation_detector`, `validation_report` |
| Reporting | `telegram_notifier`, `telegram_bot`, `weekly_report`, `report` |
| Regime | `regime_monitor` |
| Legacy / research | `momentum_live`, `momentum_backtester`, `historical_backtester`, `strategy_backtest`, `execution_engine`, `broker_simulator`, `signal_reader`, `shadow_tracker` |

Legacy modules may still run, but the live production executor is `event_trader`.

## Schedule

| Time | Job | Purpose |
|---|---|---|
| 09:45 ET | `com.ASCR-H.daily` | Primary daily execution after open |
| 12:30 ET | `com.ASCR-H.intraday` | Stop monitoring plus urgent sells and slot fills |
| Real-time | ASCR event daemon | Triggers `intraday_monitor.run_intraday_trades()` on actionable events |
| Friday 18:00 ET | `com.ASCR-H.weekly` | Weekly performance report |
| Always | `com.ASCR-H.bot` | Telegram command handler |

## Strategy Parameters

Configured in `config.yaml`.

| Parameter | Current value |
|---|---:|
| Initial cash | `$10,000` |
| Max positions | `10` |
| Target per position | `10%` of total equity |
| Minimum trade | `$50` |
| Hard stop | `-20%` from entry |
| Trailing activation | `+20%` from entry |
| Trailing stop | `-25%` from peak |
| Daily turnover cap | `30%` of portfolio value |

Sell logic is price-only in the live strategy: no profit taking, no time stop, no sell because rank or momentum dropped.

## Trading Rules

Rules are enforced in `src/trading_rules.py`.

| Rule | Behavior |
|---|---|
| Market hours | Trades only during Mon-Fri 09:30-16:00 ET |
| Holidays | 2026 NYSE holidays are skipped |
| PDT | Sub-$25K account avoids normal same-day round trips |
| T+1 settlement | Recent sell proceeds are flagged while unsettled |
| Duplicate buy | Max one buy per ticker per day |
| Sell cooldown | Avoid rebuying a ticker within 3 days of selling |
| Daily trade limit | Max 4 trades per day |
| Daily turnover | Max 30% turnover per day |
| Wash sale | Warning only on rebuy after loss within 30 days |
| Gap risk | Warning when sell price gaps through stop |

`validate_trade_full()` is the preferred validator for execution paths.

## Telegram Bot

Bot: ASCR-H Bot (`@ASCR-H Bot_bot`)

View commands:

- `/positions`
- `/summary`
- `/compare`
- `/events`
- `/ranking`
- `/regime`
- `/system`
- `/help`

Manual action commands:

- `/buy TICKER`
- `/sell TICKER`
- `/sell all`
- `/add TICKER`
- `/remove TICKER`

Manual buy/sell commands now run through the same full rule validator used by the executor. Universe add/remove edits ASCR's universe config.

## Runtime State

Runtime state is intentionally local and ignored by Git:

- `data/ascr_h.sqlite` - live paper account DB
- `logs/*.err`, `logs/*.log` - daemon and command logs
- `__pycache__/`, `*.pyc` - Python cache

Current local DB snapshot at the time of this update:

- Open positions: `ETR`, `HUT`, `IREN`, `LITE`, `NVTS`, `SBGSY`, `SMH`, `TSEM`, `VRT`
- Closed positions: `9`
- Orders: `31`
- Decision records: `916`
- Cash: `$0`
- Peak equity: about `$11,239`

These numbers are operational state, not source truth. Use `python3 -m src.main status` for current values.

## Logging

Logging is intentionally verbose on write paths:

- DB writes log cash, orders, positions, equity curve, shadow updates, and peak updates.
- Price fetch failures log ticker, source, and fallback behavior.
- Telegram sends log skipped, success, and failure states without logging full message bodies.
- Manual Telegram trades log fills, proceeds, P&L, and cash impact.
- Weekly/event/intraday integrations log failed optional sections instead of silently passing.

The logs remain local and are ignored by Git.

## CLI

```bash
python3 -m src.main init --cash 10000
python3 -m src.main run-daily
python3 -m src.main status
python3 -m src.main orders
python3 -m src.main report
python3 -m src.main weekly-report
python3 -m src.main regime-check
python3 -m src.main run-live
python3 -m src.main run-backtest --start 2025-07-01 --end 2025-12-31
python3 -m src.main evaluate-outcomes
python3 -m src.main compute-dqs
python3 -m src.main strategy-health --mode live_paper
python3 -m src.main validation-report --mode live_paper
python3 -m src.main missed-opportunities --mode live_paper
python3 -m src.main worst-decisions --mode live_paper
python3 -m src.main decisions --mode live_paper
```

## Setup

```bash
pip install pyyaml requests yfinance
python3 -m src.main init --cash 10000
python3 -m src.main run-daily
```

External dependency:

```text
${ASCR_PROJECT_DIR}
```

ASCR-H imports ASCR's recommender and reads ASCR's SQLite database.

## Verification

Current lightweight checks:

```bash
python3 tests/test_validation.py
python3 tests/test_execution.py
python3 -m py_compile src/db.py src/event_trader.py src/intraday_monitor.py src/telegram_bot.py
```

`pytest` is not currently required by `requirements.txt`; the existing tests can be run directly.
