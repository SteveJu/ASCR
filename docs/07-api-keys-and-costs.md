# API Keys And Cost Controls

ASCR uses external APIs for public market data, LLM analysis, and optional Telegram notifications.

Users bring their own keys. The public repo should never include real credentials.

## Required And Optional Keys

| Key | Required | Purpose |
|---|---:|---|
| `GEMINI_API_KEY` | one LLM key required | structured event analysis and summarization |
| `ANTHROPIC_API_KEY` | optional | filtering, review, or higher-quality analysis tasks |
| `SEC_USER_AGENT_EMAIL` | recommended | identifies your SEC EDGAR requests |
| `TELEGRAM_ASCR_BOT_TOKEN` | optional | ASCR bot notifications |
| `TELEGRAM_ASCR_H_BOT_TOKEN` | optional | ASCR-H paper execution bot |
| `TELEGRAM_CHAT_ID` | optional | Telegram destination |

## Local `.env`

Copy:

```bash
cp .env.example .env
```

Fill in:

```bash
GEMINI_API_KEY=<your-key>
ANTHROPIC_API_KEY=<your-key>
SEC_USER_AGENT_EMAIL=<your-email>
TELEGRAM_ASCR_BOT_TOKEN=<your-token>
TELEGRAM_ASCR_H_BOT_TOKEN=<your-token>
TELEGRAM_CHAT_ID=<your-chat-id>
```

Do not commit `.env`.

## Cost Philosophy

ASCR should be cheap to run because most work is done before calling an LLM:

```text
fetch public data
-> quant headline scoring
-> deduplicate by hash
-> batch filter only material, likely-incremental items
-> deep LLM analysis only on high-value candidates
-> log token usage and cost
```

## News-Routing Controls

The v1.8 headline router can be tuned from the environment:

```bash
ASCR_PREFILTER_MIN_SCORE=50
ASCR_PREFILTER_MAX_PER_BATCH=12
ASCR_RELEVANT_MAX_PER_RUN=45
```

Raise `ASCR_PREFILTER_MIN_SCORE` to reduce noise. Lower it when you prefer
broader discovery and are willing to spend more LLM budget.

## LLM Usage Logging

The ASCR DB should include an `llm_calls` table or equivalent local log with:

| Field | Meaning |
|---|---|
| `model` | model used |
| `purpose` | event analysis, headline filter, translation, review, etc. |
| `ticker` | optional affected ticker |
| `input_tokens` | prompt tokens |
| `output_tokens` | completion tokens |
| `cost_usd` | estimated call cost |
| `called_at` | timestamp |

Useful query:

```sql
SELECT model, purpose,
       COUNT(*) AS calls,
       ROUND(SUM(cost_usd), 4) AS cost
FROM llm_calls
GROUP BY model, purpose
ORDER BY cost DESC;
```

## Suggested Cost Controls

- cap articles per scan
- deduplicate headlines before LLM calls
- use quant headline scoring before LLM calls
- use cheaper models for filtering
- use stronger models only for high-impact events
- log every successful LLM call
- set daily budget alerts
- keep raw data local

## Telegram Cost And Safety

Telegram is free, but bot tokens are credentials.

If a token leaks:

1. revoke it with BotFather
2. generate a new token
3. update your local `.env`
4. confirm the old token no longer works

Never paste a real token into an issue, README, screenshot, or commit.
