# Stock-Updates-SMS

A fully automated daily market summary, pushed to your phone via Telegram every weekday morning. **100% free** — it runs on GitHub Actions and uses GitHub Models for the AI summary, so there are no API bills, no credit card, and no laptop required.

## How it works

```
GitHub Actions (cron, weekdays 7:30 AM ET)
        │
        ▼
market_summary.py
  1. Pulls SPY + watchlist prices via yfinance
  2. Pulls macro headlines from financial RSS feeds (CNBC, MarketWatch, Yahoo)
  3. Sends the raw data to an LLM (GitHub Models, free) to condense into one clean message
  4. Telegram bot pushes it to your phone
```

The AI step uses [GitHub Models](https://docs.github.com/en/github-models), GitHub's free inference API. The workflow's built-in token gets access automatically via the `models: read` permission — no key to create. If the model call ever fails (e.g. rate limit), the script sends the raw price/headline briefing instead, so you always get your message.

## Setup

### 1. Create your Telegram bot (free, ~5 minutes)

1. Install Telegram on your phone if you don't have it.
2. Message [@BotFather](https://t.me/botfather), send `/newbot`, and follow the prompts (pick any name, e.g. "Market Summary"). BotFather replies with a **bot token** like `123456789:AAF...` — save it.
3. Open a chat with your new bot (BotFather gives you a link to it) and send it any message, e.g. "hi". This is required so the bot is allowed to message you.
4. Get your **chat id**: visit this URL in a browser, with your token filled in:

   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   Find `"chat":{"id":123456789,...}` in the response — that number is your chat id.

### 2. Add repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**. Add both:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id from step 1.4 |

That's it — the AI summary needs no key at all.

### 3. (Optional) Customize the watchlist

Default is `SPY, QQQ, DIA, AAPL, NVDA, MSFT`. To change it, go to **Settings → Secrets and variables → Actions → Variables tab → New repository variable**, name it `WATCHLIST`, and set it to a comma-separated list like `SPY,QQQ,TSLA,AMD`.

### 4. Test it

Go to the **Actions** tab → **Daily market summary** → **Run workflow**. Check the *dry run* box to print the summary in the logs without sending anything, or leave it unchecked for a real end-to-end test — you should get a Telegram message within a minute.

After that, it runs automatically every weekday at 7:30 AM ET — nothing else to do.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python market_summary.py   # prints the raw briefing, no keys needed
```

To test the AI step locally, set `GITHUB_TOKEN` to a fine-grained personal access token with the `models: read` permission.

## Tweaking

- **Schedule** — edit the `cron` line in `.github/workflows/daily-summary.yml` (times are UTC; 11:30 UTC = 7:30 AM ET in summer).
- **Model** — set an `LLM_MODEL` repository variable (same Variables tab as the watchlist). Any id from the [GitHub Models catalog](https://github.com/marketplace/models) works, e.g. `openai/gpt-4o` or `meta/llama-3.3-70b-instruct`. Default is `openai/gpt-4o-mini`.
- **News sources / keyword filter** — edit `RSS_FEEDS` and `MACRO_KEYWORDS` at the top of `market_summary.py`.
- **Summary style / length** — edit the system prompt in `summarize_with_llm()`.
