# Database And Logs

ASCR is local-first. The public version should create local SQLite databases and local log files on the user's machine.

No database or log file should be committed to git.

## Directory Layout

Recommended runtime layout:

```text
data/
  ascr.sqlite
  ascr_h.sqlite

logs/
  ascr.log
  ascr.err
  ascr-h.log
  ascr-h.err
```

These paths are ignored by `.gitignore`.

## Environment Variables

Optional overrides:

```bash
ASCR_DB_PATH=./data/ascr.sqlite
ASCR_H_DB_PATH=./data/ascr_h.sqlite
```

If unset, the code should default to the local `data/` directory.

## Create Runtime Directories

```bash
mkdir -p data logs
```

## ASCR Database Purpose

The ASCR brain database stores research state:

- tickers and universe metadata
- price history
- events
- scores
- rankings
- LLM usage and estimated cost
- alerts and reports

Important conceptual tables:

| Table | Purpose |
|---|---|
| `events` | structured news, SEC, price, and supply-chain events |
| `scores` | per-ticker evidence, asymmetry, momentum, risk, and rating |
| `prices` | historical and latest price bars |
| `llm_calls` | model, purpose, token usage, and estimated cost |
| `activity` | optional operational audit log |

## ASCR-H Database Purpose

The ASCR-H hands database stores paper execution state:

- account cash
- simulated orders
- open and closed positions
- equity curve
- decisions
- validation results
- strategy health metrics

Important conceptual tables:

| Table | Purpose |
|---|---|
| `account` | simulated cash and account settings |
| `orders` | paper buy/sell records |
| `positions` | open and closed positions |
| `equity_curve` | daily portfolio value |
| `decisions` | why an action was taken or skipped |

## Initialization Flow

After code migration, the intended initialization flow is:

```bash
mkdir -p data logs
python3 -m ASCR.src.main init-db
python3 -m ASCR-H.src.main init --cash 10000
```

Until the sanitized code is migrated, treat these commands as the public interface target.

## Logging Policy

Logs should help debugging without leaking secrets.

Safe to log:

- module name
- ticker
- event id or hash
- status code
- model name
- token counts
- estimated cost
- whether Telegram send succeeded

Do not log:

- API keys
- Telegram tokens
- full `.env`
- private chat ids
- complete raw LLM prompts if they contain private notes
- full Telegram message payloads if they contain private workflow text

## Reset Local Runtime State

For a clean local test:

```bash
rm -rf data logs
mkdir -p data logs
```

This should never affect source code.

