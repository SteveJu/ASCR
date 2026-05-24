# ASCR-H v1.7 Design

v1.7 adds quote-aware position status reporting on top of the v1.6 dynamic US equity market holiday hardening.

## 1. Design Goal

ASCR-H is a live paper execution layer for ASCR.

The core constraint is separation of responsibility:

- ASCR decides what should be bought, sold, or held.
- ASCR-H decides whether a requested trade is executable under real-world constraints.
- ASCR-H sizes and records the simulated trade.
- ASCR-H does not create independent buy/sell judgment in the production path.

This keeps the system debuggable. When a trade happens, the reason should trace back to a Radar instruction plus a ASCR-H rule decision.

## 2. Production Architecture

```text
ASCR
  src/recommender.py
    get_portfolio_instructions(current_positions, max_pos)
      -> buys
      -> sells
      -> holds
      -> optional bubble/regime metadata

ASCR-H
  src/event_trader.py
    run_daily()
      -> validate rules
      -> execute sells first
      -> execute buys second
      -> log decisions
      -> update equity
      -> send Telegram summary

  src/intraday_monitor.py
    run_intraday_check()
      -> monitor stops and peak prices
      -> send alerts

    run_intraday_trades()
      -> execute urgent sells only
      -> fill empty slots from Radar buys

  src/quote_provider.py
    get_live_quote()
      -> current price
      -> previous session close when the quote source provides it
      -> DB fallback for current price only
```

## 3. Execution Flows

### Status Reporting

`python3 -m src.main status` refreshes positions and then formats the local
portfolio using `quote_provider.get_live_quote()`. Daily change is calculated
from the live quote's previous close when available. If ASCR's local price DB is
used as a fallback, ASCR-H intentionally suppresses daily change instead of
treating the last research DB row as yesterday's close.

### Daily Run

Scheduled at 09:45 ET.

```text
1. Verify trading day.
2. Load open positions and account cash.
3. Fetch current prices and refresh position values.
4. Build current_positions payload for Radar.
5. Call ASCR recommender.
6. Process sells first:
   - verify position exists
   - run validate_trade_full()
   - fetch live price
   - close position
   - update cash
   - add order
   - log decision
7. Process buys:
   - check remaining slots
   - run validate_trade_full()
   - fetch live price
   - size to min(total_equity * 10%, cash)
   - open position
   - update cash
   - add order
   - log decision
8. Record equity curve.
9. Send Telegram summary.
10. Run outcome, DQS, regime, and strategy health updates from CLI wrapper.
```

### Intraday Check

Scheduled at 12:30 ET.

```text
1. Verify market is open.
2. Fetch each open position price.
3. Update peak price if needed.
4. Alert if near or through hard/trailing stop.
5. Send Telegram monitor summary if alerts exist.
```

### Intraday Trades

Used by schedule and ASCR event daemon trigger.

```text
1. Verify market is open.
2. Load current positions and equity.
3. Ask Radar for fresh instructions.
4. Execute only urgent sells from Radar.
5. Fill empty slots from Radar buy list.
6. Send Telegram trade summary if actions occurred.
```

## 4. Trading Rules

Implemented in `src/trading_rules.py`.

`validate_trade_full()` is the full execution validator and should be used by all trade paths.

| Rule | Effect |
|---|---|
| Market hours | Blocks trades outside regular market hours |
| Trading day | Blocks weekends and dynamically generated US market holidays |
| PDT | Blocks normal same-day round trips for sub-$25K accounts |
| T+1 settlement | Allows but flags unsettled proceeds |
| Duplicate buy | Blocks repeat buys in the same ticker on same date |
| Sell cooldown | Blocks rebuy within 3 days after sell |
| Daily trade limit | Blocks after 4 total trades in one day |
| Daily turnover | Blocks above 30% daily turnover |
| Gap risk | Warning for stop sells executed below target stop |
| Wash sale | Warning for rebuy within 30 days of loss sale |

Automated execution uses `validate_trade_full()`. Any local/manual command overlay should use the same validator before writing orders.

## 5. Position Sizing

Configured in `config.yaml`.

```yaml
initial_cash: 10000
sizing:
  per_position_pct: 0.10
  max_positions: 10
```

Sizing formula:

```text
target_amount = total_equity * per_position_pct
actual_amount = min(target_amount, available_cash)
minimum_trade = 50
```

If available cash is below the minimum trade threshold, ASCR-H skips the buy and logs/sends an alert when applicable.

## 6. Stops and Exits

Production strategy is price-only.

```yaml
sell:
  hard_stop_pct: -20
  trailing_stop_activation_pct: 20
  trailing_stop_from_peak_pct: -25
```

Rules:

- Hard stop: sell when price is down 20% from entry.
- Trailing stop activates after +20% from entry.
- Once active, sell if price falls 25% from peak.
- No profit taking.
- No time stop.
- No sell due only to momentum score or rank decline.

Sector-adjusted thresholds may be calculated in ASCR. ASCR-H records and enforces the instruction it receives.

## 7. Database

Local SQLite DB:

```text
data/ascr_h.sqlite
```

Tracked tables:

| Table | Purpose |
|---|---|
| `account` | Cash and peak equity |
| `paper_positions` | Open and closed position state |
| `paper_orders` | Executed paper orders |
| `paper_equity_curve` | Daily NAV snapshots |
| `decisions` | Decision audit trail |
| `decision_outcomes` | Forward return outcomes |
| `decision_quality_scores` | DQS per decision/outcome |
| `strategy_health` | Aggregated strategy health snapshots |
| `pending_trades` | Blocked trades pending future re-evaluation |
| `shadow_tracks` | Shadow tracking records |

The DB is runtime state. It stays local and is ignored by Git.

## 8. Decision Quality Pipeline

Decision validation modules:

- `decision_logger.py` records BUY/SELL/HOLD/NO_BUY decisions.
- `outcome_evaluator.py` computes 5/10/20/60 day forward returns.
- `decision_quality.py` scores decisions.
- `strategy_health.py` aggregates health metrics.
- `degradation_detector.py` raises warnings on strategy decay.
- `validation_report.py` produces the full validation report.

The primary health formula:

```text
Overall DQS =
  25% Buy
  20% Sell
  10% Trim
  15% Hold
  15% NoBuy
  10% Ranking Quality
   5% Stability
```

## 9. Logging and Observability

ASCR-H logs to stderr/stdout; launchd or shell redirection writes these into `logs/`.

Important logging behavior:

- DB write functions log cash changes, position changes, orders, equity snapshots, shadow updates, and peak updates.
- Price fetchers log failures and DB fallbacks.
- Telegram notifier logs disabled, missing config, send success, and send failure by chunk.
- Manual/local overlay trades should log fills, proceeds, realized P&L, and cash impact.
- Optional weekly report sections log failures instead of silently passing.
- Outcome and benchmark price lookups log missing source data.

Runtime logs are ignored by Git:

```text
logs/*.log
logs/*.err
```

## 10. Telegram

The public source includes notification helpers, not the private interactive command bot.

If you build your own command layer:

- read-only commands can expose positions, summary, comparison, events, ranking, regime, system, and help views
- write commands such as buy/sell/add/remove must run through `validate_trade_full()`
- trade commands should affect only `ascr_h.sqlite`
- universe commands should affect only ASCR config
- tokens, chat ids, and localized personal wording must stay local

## 11. Git Hygiene

Tracked source:

- Python source
- project docs
- config files
- tests

Ignored runtime files:

- `data/*.sqlite`
- SQLite WAL/journal/shm files
- `logs/*.log`
- `logs/*.err`
- `__pycache__/`
- `*.pyc`

The DB and logs can be useful for local analysis, but they should not be pushed.

## 12. Known Legacy Areas

The repository still contains older research/execution code:

- `momentum_live.py`
- `momentum_backtester.py`
- `historical_backtester.py`
- `strategy_backtest.py`
- `execution_engine.py`
- `broker_simulator.py`
- `signal_reader.py`

These may use older config assumptions. Treat `event_trader.py` plus `intraday_monitor.py` as the live production execution path.
