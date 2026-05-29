# Strategy Thinking

ASCR is built around information flow.

The core hypothesis:

> In a fast-moving infrastructure buildout, supply-chain consequences are often distributed across companies before they are obvious in consensus numbers.

## Example

If a hyperscaler increases AI data center capex, the obvious market reaction may focus on GPUs. ASCR asks what else changes:

- memory demand
- optical interconnect demand
- power and cooling demand
- equipment utilization
- data center capacity
- grid constraints

The system tries to convert that chain into structured evidence.

## Signal Types

High-value signals:

- contracts
- customer wins
- capacity expansions
- 8-K material agreements
- earnings guidance tied to AI demand
- insider clusters
- supply shortages
- credible counterparty mentions

Lower-value signals:

- generic analyst upgrades
- repeated known news
- vague AI marketing language
- pure price momentum without a new event

## News Triage

ASCR treats news as a data stream, not reading material. The first question is:

> Is this incremental, material information that could change expectations for a tracked company or its direct supply chain?

The v1.8 router assigns a `quant_score` before any deep LLM call. It favors:

- named customer wins or losses
- material contracts, supply agreements, prepayments, and capacity reservations
- guidance changes, earnings surprises, backlog, bookings, and explicit magnitude
- SEC filings, funding, dilution, auditor or management red flags
- supply shortages, export controls, and regulation
- major counterparty signals from hyperscalers, NVIDIA, AMD, Broadcom, TSMC, Samsung, or SK Hynix

It penalizes:

- analyst target-price notes without new facts
- "best AI stocks" lists and generic stock-pick content
- vague AI hype
- syndicated market recaps
- generic 13F notices that do not name a position change

This is still a routing layer, not a complete alpha model. The stronger version
would combine the routed news with price reaction, volume, options volatility,
and historical event-study calibration.

## Scoring Philosophy

Each event gets an `evidence_delta`.

Multiple weak events should not automatically equal one strong thesis. ASCR caps event-count amplification and prefers diverse, specific event types.

The current public scoring stack has five layers:

- base features: evidence, asymmetry, momentum, and risk
- event alpha: time-decayed, source-weighted public events with verdict, conviction, confidence, and priced-in adjustment
- valuation overlay: a bounded sanity check that emphasizes P/S, EV/Sales, EV/EBITDA, and price/free-cash-flow more than P/E
- business-quality overlay: a bounded check on margins, free cash flow, ROE, leverage, and growth quality
- feedback alpha: optional ASCR-H outcome feedback with sample-size shrinkage and hard caps

This is intentionally not a conservative value strategy. ASCR is meant to find
high-risk re-rating candidates, so high P/E by itself should not kill a signal.
The more useful question is whether the event is already priced into sales,
enterprise value, cash flow, and balance-sheet risk.

Default production weights are currently pinned to the strict as-of ablation winner:

```yaml
evidence: 0.35
asymmetry: 0.30
momentum: 0.25
risk: -0.10
```

Feedback validation is strict as-of: each historical scoring date can only use ASCR-H outcomes whose evaluation date was already known by that date.

The recommended ranking flow is:

```text
structured event
  -> ticker
  -> evidence_delta
  -> verdict and conviction
  -> event score
  -> ranking
  -> portfolio intent
```

## Risk Controls

ASCR-H exists because signal quality is not enough. A usable system also needs:

- max position count
- per-position sizing
- hard stops
- trailing stops
- daily trade limits
- sell cooldown
- turnover caps
- transparent logs

The public project should make both the thesis and the execution consequences visible.

## Backtest Interpretation

Backtests should be read by source profile, not as one blended headline number:

- `sec_only`: asks whether auditable SEC filing events alone carried useful signal.
- `sec_form4_13f`: adds insider and institutional confirmation signals, with the constraint that 13F data can only be used after filing date.
- `news_exploratory`: adds historical news-style signals, but treats them as exploratory unless backed by a stable historical news provider.

The live-style replay is the preferred realism check because it starts with no memory and lets ASCR-H-style rules reject trades. A strategy that only works before execution constraints is not a usable strategy.
