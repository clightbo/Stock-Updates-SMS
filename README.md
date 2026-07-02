# Stock-Updates-SMS

A fully automated daily market summary, texted to your phone every weekday morning. No laptop required — it runs in the cloud for free on GitHub Actions.

## How it works

```
GitHub Actions (cron, weekdays 7:30 AM ET)
        │
        ▼
market_summary.py
  1. Pulls SPY + watchlist prices via yfinance
  2. Pulls macro headlines from financial RSS feeds (CNBC, MarketWatch, Yahoo)
  3. Sends the raw data to Claude, which condenses it into one clean text
  4. Twilio delivers it to your phone as an SMS
```

## Setup

### 1. Get your API keys

- **Anthropic** — create a key at [console.anthropic.com](https://console.anthropic.com/settings/keys). Each daily summary costs a fraction of a cent.
- **Twilio** — sign up at [twilio.com](https://www.twilio.com/try-twilio), get a phone number (trial credit covers it), and grab your **Account SID** and **Auth Token** from the console dashboard. On a trial account you must also verify your personal number under *Phone Numbers → Verified Caller IDs*.

### 2. Add repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**. Add all five:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID (starts with `AC`) |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | Your Twilio number, e.g. `+15551234567` |
| `SMS_TO_NUMBER` | Your phone, e.g. `+15559876543` |

### 3. (Optional) Customize the watchlist

Default is `SPY, QQQ, DIA, AAPL, NVDA, MSFT`. To change it, go to **Settings → Secrets and variables → Actions → Variables tab → New repository variable**, name it `WATCHLIST`, and set it to a comma-separated list like `SPY,QQQ,TSLA,AMD`.

### 4. Test it

Go to the **Actions** tab → **Daily market summary** → **Run workflow**. Check the *dry run* box to print the summary in the logs without sending a text, or leave it unchecked for a real end-to-end test.

After that, it runs automatically every weekday at 7:30 AM ET — nothing else to do.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python market_summary.py            # no keys needed, prints raw briefing
DRY_RUN=1 ANTHROPIC_API_KEY=sk-... python market_summary.py   # prints Claude's summary
```

## Tweaking

- **Schedule** — edit the `cron` line in `.github/workflows/daily-summary.yml` (times are UTC; 11:30 UTC = 7:30 AM ET in summer).
- **News sources / keyword filter** — edit `RSS_FEEDS` and `MACRO_KEYWORDS` at the top of `market_summary.py`.
- **Summary style / length** — edit the system prompt in `summarize_with_claude()`.
