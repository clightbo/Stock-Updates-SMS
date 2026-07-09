# Stock-Updates-SMS

Personal automation pipelines that run free in the cloud on GitHub Actions and message your phone via Telegram:

1. **Daily market summary** — watchlist prices + AI-condensed macro news, weekday mornings ([setup](#market-summary))
2. **Daily AI agenda** — an AI chief of staff that reads your Outlook inbox and calendar and messages you a morning plan ([setup](#daily-ai-agenda))
3. **Notion planner sync** — an AI scheduling bot that turns your email and calendar into an organized planner board in Notion ([setup](#notion-planner))

<a name="market-summary"></a>

## Daily market summary

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
  5. (optional) Saves the day's briefing to a **Market Daily** database in Notion
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

### Notion finance log (optional)

If you already set up Notion for the [planner sync](#notion-planner), the market summary reuses the same `NOTION_TOKEN` and `NOTION_PARENT_PAGE_ID` secrets. Each weekday run adds one row to a **Market Daily** database (created automatically on first run) with the date, AI summary, and watchlist snapshot. Re-runs on the same day are skipped so you never get duplicates.

On your Notion hub page, you'll end up with two databases side by side:

| Database | What it holds |
|---|---|
| **AI Planner** | Tasks, deadlines, and calendar events from email |
| **Market Daily** | Daily watchlist prices + AI market briefing |

Share your hub page with the integration (**••• → Connections → your integration**) and add the two secrets if you haven't already. Then run **Daily market summary** once to see the first row appear.

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
- **Interview pitch** — rotates weekly from a curated list in `INTERVIEW_IDEAS` at the top of `market_summary.py`. Edit or add ideas there; the AI ties it to today's headlines when it can.

<a name="daily-ai-agenda"></a>

## Daily AI agenda

Every morning at 7:00 AM ET, an AI reads your Outlook calendar (next 48 hours) and inbox (last 24 hours), figures out what actually needs your attention, and sends you a Telegram message with three sections: **TODAY** (your schedule), **NEEDS ACTION** (emails with deadlines or that need replies), and **SHOULD SCHEDULE** (things from email that belong on your calendar, with suggested free slots). Newsletters and promo noise are filtered out by the AI.

```
GitHub Actions (cron, daily 7:00 AM ET)
        │
        ▼
daily_agenda.py
  1. Pulls your calendar + recent inbox via Microsoft Graph
  2. AI (GitHub Models, free) turns it into a prioritized morning plan
  3. Telegram bot pushes it to your phone
```

### Setup (builds on the market summary setup)

You already have the Telegram bot and secrets from the market summary. Two additions:

**1. Add a `GH_PAT` secret.** This lets the pipeline keep your Microsoft sign-in fresh automatically (Microsoft rotates tokens; this stores the new one each run).

1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new) (Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token).
2. Note: anything, e.g. `agenda pipeline`. Expiration: **No expiration**. Scopes: check the top-level **repo** box. Click **Generate token**.
3. Copy the token (starts with `ghp_`) and add it as a repository secret named `GH_PAT`.

**2. Sign in to Microsoft (once).**

1. Go to the **Actions** tab → **Microsoft sign-in (run once)** → **Run workflow**.
2. Open the running job's log (click the run, then **device-login**). Within a few seconds it prints a link (`https://login.microsoft.com/device`) and a short code.
3. On your phone, open the link, enter the code, and sign in with the Microsoft account whose Outlook you want summarized. Approve the permissions (read your mail and calendar).
4. The log will print "Success! Signed in as ..." and store the token as a secret automatically.

Then test it: **Actions → Daily AI agenda → Run workflow**. It runs daily at 7:00 AM ET after that.

### Notes

- **Privacy:** email subjects and previews are sent to GitHub Models (GitHub's AI service) to build the summary. They are never printed in workflow logs.
- **No Azure setup needed:** sign-in uses Microsoft's public "Graph Command Line Tools" app id. If your account blocks it (some school/work accounts do), you'd need your own (free) app registration — open an issue or ask your AI assistant.
- **Timezone:** default is US Eastern. Set a `TIMEZONE` repository variable to change it (Windows format, e.g. `Pacific Standard Time`).
- **Sample data:** the manual run has a "sample data" checkbox that uses built-in fake email/calendar data — useful for testing the pipeline before signing in to Microsoft.

<a name="notion-planner"></a>

## Notion planner sync

Twice a day (7:30 AM and 5:00 PM ET), an AI reads your Outlook inbox and calendar and keeps a planner database in Notion up to date — your "hub". Calendar events for the next week land as **Event** rows, and the AI extracts real to-dos from email (deadlines, meetings people proposed, things you committed to) as **Task / Deadline / Follow-up / Meeting to schedule** rows. **Important financial announcements** (US macro releases + watchlist earnings) are also added to **Outlook calendar** and Notion as **Markets** events. Newsletters and promo noise are skipped. Every row has a Status (Inbox → Planned → Done) so you can run your week from one board. Nothing is ever added twice — each row carries a hidden dedupe key.

```
GitHub Actions (cron, 7:30 AM + 5:00 PM ET)
        │
        ▼
planner_sync.py
  1. Pulls calendar (next 7 days) + inbox (last 24h) via Microsoft Graph
  2. Pulls US macro releases (high/medium + Fed) and watchlist earnings (next 7 days)
  3. Adds market events to Outlook calendar (30-min blocks, 30-min reminder)
  4. AI (GitHub Models, free) extracts tasks, deadlines & meetings from email
  5. Upserts everything into your "AI Planner" database in Notion
  6. (optional) Telegram ping listing what was added
```

### Setup (builds on the daily agenda setup)

You already have the Microsoft sign-in, `GH_PAT`, and Telegram secrets from the agenda pipeline. Two additions, both free (~5 minutes):

**1. Create a Notion integration.**

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**. Name it anything (e.g. "AI Planner"), pick your workspace, and under Capabilities make sure **Read**, **Update**, and **Insert content** are enabled.
2. Copy the **Internal Integration Secret** (starts with `ntn_` or `secret_`) and add it as a repository secret named `NOTION_TOKEN`.

**2. Pick (or create) a Notion page to hold the planner.**

1. In Notion, create a page (e.g. "Planner Hub") or pick an existing one.
2. On that page: **••• menu → Connections → Add connection →** your integration. (Without this the bot can't see the page.)
3. Copy the page's URL (**••• menu → Copy link**) and add it as a repository secret named `NOTION_PARENT_PAGE_ID` (the full URL is fine — the id is extracted automatically).

On the first run the bot creates an **"AI Planner"** database under that page with Name, Type, Status, Due, Source, From, and Notes columns. Later runs find it by title and only add what's new — you can move it, add views (a calendar view on the **Due** property works great), or add your own columns freely.

Then test it: **Actions → Notion planner sync → Run workflow**. Check *dry run* to see what would be added without writing anything, or run it for real and watch the rows appear in Notion. After that it runs automatically twice a day.

**Calendar write permission:** market events are created in Outlook with subjects like `[Markets] CPI`. If you signed in to Microsoft before this feature existed, re-run **Microsoft sign-in (run once)** so the token includes calendar write access.

### Notes

- **You stay in control:** the bot only adds rows. It never edits or deletes anything, so your statuses, notes, and re-ordering are safe.
- **Privacy:** like the agenda, email subjects/previews go to GitHub Models for extraction and are never printed in workflow logs.
- **Schedule:** edit the two `cron` lines in `.github/workflows/planner-sync.yml` (times are UTC).
