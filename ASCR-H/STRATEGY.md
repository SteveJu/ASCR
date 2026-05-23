# AI Momentum Sprint Strategy

## Objective

Run a $10K paper-traded momentum strategy focused on the AI infrastructure buildout.

This is not a diversified investment strategy. It is a concentrated speculative strategy designed to test whether ASCR's AI infrastructure signals can be converted into a repeatable execution process.

## Current Live Configuration

Source of truth: `config.yaml`.

| Parameter | Value |
|---|---:|
| Initial bankroll | `$10,000` |
| Max positions | `10` |
| Target position size | `10%` of total equity |
| Minimum momentum | `50` |
| Minimum trade | `$50` |
| Hard stop | `-20%` from entry |
| Trailing activation | `+20%` from entry |
| Trailing stop | `-25%` from peak |

## Buy Rules

ASCR decides the buy list. ASCR-H executes subject to account constraints.

Operational rules:

1. Ask Radar for portfolio instructions.
2. Only buy tickers returned in Radar's `buys`.
3. Do not buy if already held.
4. Do not exceed 10 open positions.
5. Size each position to roughly 10% of total equity.
6. If cash is lower than target allocation, use available cash.
7. Skip if trade amount is below $50.
8. Enforce full trading rules before order creation.

ASCR-H does not locally rank or override Radar's buy list in the production path.

## Sell Rules

Sell logic is price-only.

1. Hard stop: sell at -20% from entry.
2. Trailing stop: after +20% gain, sell when price falls 25% from peak.

Explicitly not part of the live sell strategy:

- No profit taking at +25%, +50%, or +100%.
- No time stop.
- No sell just because momentum score dropped.
- No sell just because rank dropped.
- No averaging down.

## Intraday Rules

The 12:30 ET intraday job has two roles:

1. Monitor open positions for hard stop, trailing stop, and large move alerts.
2. Execute urgent Radar sells and fill open slots from Radar buys.

Intraday sells should only execute urgent instructions such as stop-loss events. Non-urgent rotation remains a daily execution concern.

## Risk Controls

ASCR-H enforces trading constraints before any simulated order:

- Market hours only.
- NYSE holidays skipped.
- PDT protection for sub-$25K account.
- T+1 settlement warning.
- Duplicate same-day buy block.
- 3-day sell cooldown before rebuy.
- 4-trade daily limit.
- 30% daily turnover limit.
- Wash-sale warning.
- Gap-through-stop warning.

The system is intentionally conservative on execution constraints. If the rule layer blocks a trade, the trade should be logged as blocked rather than forced.

## Regime Monitor

Run:

```bash
python3 -m src.main regime-check
```

Kill/caution signals:

- AI universe underperforms SPY by 10%+.
- More than 70% of the universe is negative with weak average returns.
- System buys underperform random universe picks.
- Consecutive losing sells accumulate.

Recommendations are advisory. The strategy does not automatically liquidate unless Radar/executor sell instructions trigger and pass validation.

## Decision Quality

The strategy is evaluated through DQS rather than only P&L.

Run:

```bash
python3 -m src.main evaluate-outcomes
python3 -m src.main compute-dqs
python3 -m src.main strategy-health --mode live_paper
python3 -m src.main validation-report --mode live_paper
```

Primary questions:

- Did buys outperform QQQ and avoid excessive drawdown?
- Did sells avoid further drawdown without excessive opportunity cost?
- Did holds continue to work?
- Did no-buy decisions avoid losers without missing large winners?
- Are rating/ranking tiers predictive?
- Is DQS stable over time?

## Local State

Runtime state is generated locally and should not be committed. Check your own paper account with:

```bash
python3 -m src.main status
```

## Legacy Backtest Note

Older backtest notes referenced a 5-position version of this strategy. The live config is now 10 positions at 10% target weight.

Historic result kept for context:

- 2025-H2 backtest: `$10,000 -> $22,314` (`+123.1%`)
- QQQ: `+12.6%`
- Alpha: `+110.6%`
- Max drawdown: `-57.5%`

Because config and execution rules have evolved, this result should not be treated as a direct live-production expectation without rerunning the current 10-position rules.

## Operating Principle

If price is holding, hold. If the price rules fail, exit. If the regime breaks, stop adding risk and review the system.
