# Public Release Checklist

Use this checklist before pushing ASCR to a public GitHub repository.

## Never Publish

- `.env`
- real Telegram bot tokens
- real API keys
- real chat ids
- local SQLite databases
- runtime logs
- local account state
- personal absolute paths such as `/Users/name/...`

## Required Sanitization

Before the first public commit:

```bash
find . -name ".env" -print
find . -name "*.sqlite*" -print
find . -name "*.db" -print
find . -type d -name logs -print
find . -type d -name data -print
```

Search for secrets:

```bash
rg -n --hidden "API_KEY|TOKEN|SECRET|PASSWORD|bot_token|chat_id|ANTHROPIC|GEMINI|TELEGRAM|sk-|AIza"
```

Search for local paths:

```bash
rg -n "/Users/|/home/|C:\\\\"
```

Search for personal emails:

```bash
rg -n "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"
```

## First Public Repo Rule

Do not push the private repo history.

Use a clean public directory and start fresh:

```bash
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ASCR.git
git push -u origin main
```

## Token Rotation

If a token was ever committed to a private repo, rotate it before going public. Private history can be accidentally pushed later.

For Telegram:

1. Open BotFather.
2. Select the bot.
3. Revoke/regenerate token.
4. Update only your local `.env` or `config/telegram.yaml`.
5. Never commit the regenerated token.

## Public Claims

When publishing backtest results:

- label them as research
- include data sources
- include known limitations
- avoid implying guaranteed returns
- make methodology reproducible
- separate backtest metrics from live paper trading metrics

## Public Universe Policy

The AI supply chain universe can be public. It is part of the research thesis and helps other users understand the system.

Safe to publish:

- sector names
- ticker lists
- benchmark tickers
- explanatory comments about why a category exists

Keep private or configurable:

- personal position sizes
- active portfolio state
- private watch notes
- Telegram chat workflows
- Chinese-localized personal bot copy unless intentionally supported
- operational logs showing what the private system did on a specific day
