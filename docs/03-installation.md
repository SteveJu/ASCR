# Installation

This guide assumes macOS or Linux with Python 3.11+.

## 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/ASCR.git
cd ASCR
```

## 2. Create A Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Create Local Config

```bash
cp .env.example .env
cp config/telegram.example.yaml config/telegram.yaml
cp config/universe.sample.yaml config/universe.yaml
```

Edit `.env`:

```bash
GEMINI_API_KEY=your_gemini_key
ANTHROPIC_API_KEY=your_anthropic_key
SEC_USER_AGENT_EMAIL=your_email@example.com
TELEGRAM_ASCR_BOT_TOKEN=your_ascr_bot_token
TELEGRAM_ASCR_H_BOT_TOKEN=your_ascr_h_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

You do not need every provider on day one. Start with one supported LLM key and add the rest later.

## 4. Create Telegram Bots

Telegram is optional, but it is the easiest way to interact with the system.

1. Open Telegram.
2. Message `@BotFather`.
3. Create a bot for Radar.
4. Create a bot for ASCR-H.
5. Put the tokens in your local `.env` or `config/telegram.yaml`.
6. Find your chat id and set `TELEGRAM_CHAT_ID`.

Never commit these tokens.

## 5. Initialize Local Databases

The public code will create local SQLite databases under `data/`.

```bash
mkdir -p data logs
```

Then run the initialization commands provided by the ASCR and ASCR-H modules.

## 6. First Run Flow

Recommended first-run order:

```text
1. initialize Radar DB
2. fetch sample prices
3. run one Radar scan
4. inspect recommendations
5. initialize ASCR-H with sample cash
6. run ASCR-H once
7. inspect positions and orders
```

## 7. Common Setup Failures

| Symptom | Likely Cause |
|---|---|
| LLM calls fail | API key missing or provider quota exhausted |
| SEC requests fail | missing or invalid `SEC_USER_AGENT_EMAIL` |
| Telegram sends fail | wrong bot token or chat id |
| no trades happen | no qualifying ASCR signal or ASCR-H rule blocks execution |
| database locked | two long-running processes writing the same SQLite DB |
