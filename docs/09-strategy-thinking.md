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

## Scoring Philosophy

Each event gets an `evidence_delta`.

Multiple weak events should not automatically equal one strong thesis. ASCR caps event-count amplification and prefers diverse, specific event types.

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
