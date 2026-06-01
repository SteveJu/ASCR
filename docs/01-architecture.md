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
  -> frontier domain and chokepoint discovery
  -> ASCR event pipeline
  -> quant headline routing
  -> valuation and business-quality sanity checks
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

ASCR includes a deterministic headline router before LLM analysis. It is not a
prediction model; it is a quality gate that decides which articles deserve
structured extraction.

The router scores:

- materiality: contracts, guidance, SEC filings, customer changes, dilution, supply disruption
- surprise and magnitude: explicit dollar amounts, percentages, backlog, bookings, shortages
- affected actor: tracked ticker, named supplier, or major counterparty
- noise: generic analyst notes, stock-pick lists, vague AI hype, syndicated recaps

Only high-scoring items move on to Gemini/Sonnet analysis. This keeps the event
store focused on information that could plausibly change expectations.

## Frontier Radar

ASCR v1.9 adds a watch-only frontier radar before the main recommendation path.
It tracks domain-tagged discovery queries, chokepoint candidates, and thesis
receipts for AI infrastructure, humanoid robotics, robotics automation,
commercial space, quantum computing, and frontier energy.

The frontier radar is not an execution path. It does not add tickers to the
trading universe automatically, does not create buy/sell intent, and does not
send orders to ASCR-H. Its job is to make early hypotheses visible and auditable
before a human decides whether they deserve promotion into the main ASCR universe.

## Fundamental Overlays

ASCR is built for high-risk AI supply-chain opportunity discovery, not a
conservative value screen. P/E is therefore a weak signal: many early re-rating
candidates have noisy, depressed, or negative earnings before the thesis becomes
visible in income statements.

The scoring engine uses bounded overlays instead:

- valuation sanity checks: P/S, EV/Sales, EV/EBITDA, and price/free-cash-flow
- business quality: margins, free-cash-flow margin, ROE, growth quality, and
  net debt/EBITDA

These overlays adjust evidence/asymmetry/risk after event alpha. They are meant
to prioritize research, not to auto-reject high-risk stocks.

## Local First

ASCR is local-first:

- SQLite for state
- local config files
- local `.env`
- optional Telegram notifications

This keeps setup simple and makes it easier to inspect the system.
