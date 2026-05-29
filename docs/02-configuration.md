# Configuration

ASCR uses local configuration files and environment variables.

## Files

```text
.env                         # local secrets, ignored by git
config/telegram.yaml          # local Telegram config, ignored by git
config/universe.yaml          # public or local research universe
config/telegram.example.yaml  # safe template
config/universe.sample.yaml   # public sample universe
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini event analysis and summarization |
| `ANTHROPIC_API_KEY` | Anthropic filtering or higher-quality review tasks |
| `SEC_USER_AGENT_EMAIL` | SEC EDGAR user-agent identity |
| `TELEGRAM_ASCR_BOT_TOKEN` | ASCR signal and status bot |
| `TELEGRAM_ASCR_H_BOT_TOKEN` | ASCR-H paper execution bot |
| `TELEGRAM_CHAT_ID` | Telegram destination chat |
| `ASCR_DB_PATH` | Optional override for ASCR SQLite path |
| `ASCR_H_DB_PATH` | Optional override for ASCR-H SQLite path |

## Local Secrets Rule

If a value can send messages, spend API credits, identify you, or reveal your account state, it belongs in `.env` or another ignored local file.

It does not belong in git.

## Universe Policy

The AI supply chain universe is allowed to be public. It is part of the strategy definition and makes the project easier to understand.

You can either:

- keep `config/universe.sample.yaml` as the public starter universe
- rename a sanitized version to `config/universe.yaml` and track it publicly

Do not include personal position state, private annotations, or operational logs in the universe file.

## Scoring Policy

`ASCR/config/scoring.yaml` controls bounded scoring overlays:

- `event_alpha`: source/type/confidence/event-decay adjustments from structured public events
- `valuation`: valuation sanity checks, with P/E intentionally de-emphasized for high-risk re-rating candidates
- `business_quality`: margins, cash generation, ROE, leverage, and growth quality
- `feedback_alpha`: optional ASCR-H outcome feedback with sample-size shrinkage

The valuation and quality overlays are research-prioritization checks. They
should not be treated as a complete fundamental valuation model or hard
portfolio decision engine.

## Localization Policy

Public ASCR documentation and default bot text should use English first.

Chinese-localized Telegram commands, personal phrasing, and private notification formats can stay in a private overlay or local config. This keeps the public repo easier for a broad audience to understand while preserving your own workflow privately.
