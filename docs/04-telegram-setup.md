# Telegram Setup

Telegram is optional, but it makes the system easier to use.

ASCR supports two-bot separation:

- ASCR bot: signals, rankings, events, system status
- ASCR-H bot: positions, orders, PnL, execution summaries

## Create Bots

1. Open Telegram.
2. Message `@BotFather`.
3. Run `/newbot`.
4. Create one bot for ASCR.
5. Create one bot for ASCR-H.
6. Copy the tokens into your local `.env`.

Example:

```bash
TELEGRAM_ASCR_BOT_TOKEN=<your-ascr-bot-token>
TELEGRAM_ASCR_H_BOT_TOKEN=<your-ascr-h-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
```

## Get Chat ID

Common methods:

- message your bot, then call Telegram `getUpdates`
- use a trusted chat-id helper bot
- log incoming Telegram updates locally during development

Do not publish your chat id if you do not want it associated with your identity.

## Security

If a token is committed by mistake:

1. revoke it in BotFather
2. generate a new token
3. remove it from git history before making a repo public
