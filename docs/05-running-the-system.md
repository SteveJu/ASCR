# Running The System

This document describes the intended operating model. Exact commands will be finalized after the sanitized ASCR and ASCR-H code is migrated into this public repository.

## Development Mode

Run pieces manually:

```text
1. run ASCR scan
2. inspect events
3. inspect rankings
4. run ASCR-H once
5. inspect simulated orders and positions
```

## Daily Mode

Recommended flow:

```text
09:45 ET  ASCR-H daily run
12:30 ET  intraday stop monitor
market hours  ASCR event daemon watches new events
Friday PM  weekly performance report
```

## Local State

Runtime state lives under ignored directories:

```text
data/
logs/
```

These are intentionally not part of the public repo.

## Debugging

The most useful debugging questions:

- Did ASCR produce a buy/sell/hold intent?
- Did ASCR-H reject it because of a rule?
- Was the price available?
- Was the market open?
- Was there enough simulated cash?
- Did Telegram fail separately from the core logic?
