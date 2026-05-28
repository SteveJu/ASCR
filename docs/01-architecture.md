# Architecture: Brain And Hands

ASCR is intentionally split into two systems.

```text
ASCR
  public information
  -> event extraction
  -> scoring
  -> ranking
  -> portfolio intent

ASCR-H
  portfolio intent
  -> trading rule validation
  -> paper sizing
  -> simulated orders
  -> positions and PnL
  -> reports and Telegram updates
```

## ASCR Responsibilities

ASCR answers:

- What changed?
- Which ticker does the event affect?
- Is it bullish, bearish, or noise?
- How strong is the evidence?
- What is the thesis?
- What should the paper executor buy, sell, or hold?

ASCR does not decide exact share size or simulate fills.

## ASCR-H Responsibilities

ASCR-H answers:

- Is the requested paper trade executable?
- How much simulated cash is available?
- How many shares should be bought or sold?
- Does this violate market-hours, daily trade, cooldown, or turnover rules?
- What is the current equity curve?
- Which decisions worked or failed later?

ASCR-H does not create independent buy/sell opinions in the main path.

## Why The Split Matters

Separating the system makes debugging possible.

If a bad trade happens, it should be traceable to one of two causes:

- ASCR produced a bad intent.
- ASCR-H executed a valid intent poorly or under weak rules.

That separation also lets users replace one side without rewriting the other. For example, someone can use ASCR with their own paper engine, or use ASCR-H with another signal source.

## Data Flow

```text
News / SEC / prices / fundamentals
  -> ASCR event pipeline
  -> quant headline routing
  -> SQLite event and score tables
  -> recommender.get_portfolio_instructions()
  -> ASCR-H daily or intraday run
  -> simulated orders and positions
  -> reports, decisions, outcomes
```

## Backtest Replay Model

The public `backtests/` package includes a live-style replay that mirrors this brain/hands split:

```text
historical events
  -> blank ASCR event memory
  -> ASCR-style ranking and portfolio intent
  -> ASCR-H-style rejection rules
  -> replay trades, blocked orders, and daily equity
```

The replay is intentionally stricter than the older simulators. It starts with no memory, applies a one-trading-day execution lag, imports trading exclusions, and records rejected orders separately.

Source profiles keep methodology comparisons explicit:

- `sec_only`
- `sec_form4_13f`
- `news_exploratory`

## News Routing

ASCR v1.8 adds a deterministic headline router before LLM analysis. It is not a
prediction model; it is a quality gate that decides which articles deserve
structured extraction.

The router scores:

- materiality: contracts, guidance, SEC filings, customer changes, dilution, supply disruption
- surprise and magnitude: explicit dollar amounts, percentages, backlog, bookings, shortages
- affected actor: tracked ticker, named supplier, or major counterparty
- noise: generic analyst notes, stock-pick lists, vague AI hype, syndicated recaps

Only high-scoring items move on to Gemini/Sonnet analysis. This keeps the event
store focused on information that could plausibly change expectations.

## Local First

ASCR is local-first:

- SQLite for state
- local config files
- local `.env`
- optional Telegram notifications

This keeps setup simple and makes it easier to inspect the system.
