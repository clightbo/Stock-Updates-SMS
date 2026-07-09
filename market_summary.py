"""Daily market summary pipeline.

Flow: fetch watchlist prices (yfinance) -> fetch macro headlines (RSS)
      -> condense with Claude -> deliver via Telegram message.

Runs on a schedule via GitHub Actions (see .github/workflows/daily-summary.yml).

Environment variables:
    GITHUB_TOKEN         Token for GitHub Models (provided automatically in
                         Actions when the workflow has `models: read` permission)
    TELEGRAM_BOT_TOKEN   Bot token from @BotFather (required unless DRY_RUN=1)
    TELEGRAM_CHAT_ID     Your chat id with the bot (required unless DRY_RUN=1)
    WATCHLIST            Comma-separated tickers (optional, defaults below)
    LLM_MODEL            GitHub Models model id (optional, default below)
    DRY_RUN              Set to "1" to print the summary instead of sending it.
    NOTION_TOKEN         Notion integration secret (optional; enables Notion sync)
    NOTION_PARENT_PAGE_ID  Notion page URL/id for the finance hub (required on
                           first run if the Market Daily database doesn't exist)
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import yfinance as yf

from notion_client import get_or_create_database, key_exists, notion_request, rich_text_chunks

DEFAULT_WATCHLIST = ["SPY", "QQQ", "DIA", "AAPL", "NVDA", "MSFT"]

RSS_FEEDS = {
    "CNBC Markets": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Economy": "https://www.cnbc.com/id/20910255/device/rss/rss.html",
    "CNBC World": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "CNBC Deals": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

# Headlines matching any of these are kept. The LLM makes the final judgment
# call on what's notable; this filter just keeps token usage down.
MACRO_KEYWORDS = [
    # macro / rates
    "fed", "fomc", "powell", "rate", "inflation", "cpi", "ppi", "pce",
    "jobs", "payroll", "unemployment", "gdp", "treasury", "yield", "bond",
    "tariff", "trade", "oil", "opec", "recession", "stimulus", "earnings",
    "market", "stocks", "s&p", "spy", "nasdaq", "dow", "rally", "selloff",
    "crash", "dollar", "ecb", "housing", "retail sales", "consumer",
    # M&A / deals
    "merger", "acquisition", "acquire", "buyout", "takeover", "deal",
    "ipo", "stake", "spinoff", "spin-off", "bid for",
    # world events
    "china", "war", "ukraine", "russia", "israel", "iran", "sanctions",
    "election", "geopolit", "nato", "summit", "north korea", "taiwan",
    "strike", "protest", "coup", "missile", "ceasefire",
]

# US economic calendar (times include ET offset), free JSON feed.
ECON_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

MAX_HEADLINES = 35
MESSAGE_CHAR_LIMIT = 2600  # Telegram allows 4096; keep it skimmable

# Free inference via GitHub Models (https://models.github.ai). Any model id
# from the catalog works, e.g. "openai/gpt-4o" or "meta/llama-3.3-70b-instruct".
LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini"
MARKET_DB_TITLE = "Market Daily"


def get_watchlist() -> list[str]:
    raw = os.environ.get("WATCHLIST", "")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return tickers or DEFAULT_WATCHLIST


def fetch_prices(tickers: list[str]) -> list[dict]:
    """Return latest close and day-over-day % change for each ticker."""
    rows = []
    data = yf.download(
        tickers, period="5d", interval="1d",
        group_by="ticker", auto_adjust=True, progress=False, threads=True,
    )
    for ticker in tickers:
        try:
            closes = (data[ticker]["Close"] if len(tickers) > 1 else data["Close"]).dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            rows.append({
                "ticker": ticker,
                "close": round(last, 2),
                "pct_change": round((last - prev) / prev * 100, 2),
            })
        except (KeyError, IndexError):
            print(f"warning: no price data for {ticker}", file=sys.stderr)
    return rows


def fetch_headlines() -> list[str]:
    """Pull recent macro-relevant headlines from financial RSS feeds."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    headlines: list[str] = []
    seen: set[str] = set()

    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            print(f"warning: failed to parse {source}: {exc}", file=sys.stderr)
            continue
        for entry in feed.entries[:30]:
            title = entry.get("title", "").strip()
            if not title or title.lower() in seen:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                published_dt = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
                if published_dt < cutoff:
                    continue
            if not any(kw in title.lower() for kw in MACRO_KEYWORDS):
                continue
            seen.add(title.lower())
            headlines.append(f"[{source}] {title}")

    return headlines[:MAX_HEADLINES]


def fetch_econ_calendar() -> list[str]:
    """Today's US economic events (data releases, Fed speakers) with ET times."""
    import requests

    try:
        events = requests.get(
            ECON_CALENDAR_URL, timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        ).json()
    except Exception as exc:
        print(f"warning: econ calendar fetch failed: {exc}", file=sys.stderr)
        return []

    # The feed's timestamps carry the ET offset, e.g. 2026-07-07T08:30:00-04:00.
    today_et = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    lines = []
    for ev in events:
        if ev.get("country") != "USD":
            continue
        date = ev.get("date", "")
        if not date.startswith(today_et):
            continue
        title = ev.get("title", "")
        impact = ev.get("impact", "")
        # Keep high/medium impact releases, plus anything Fed-related.
        fed_related = "fomc" in title.lower() or "fed" in title.lower()
        if impact not in ("High", "Medium") and not fed_related:
            continue
        time_et = date[11:16]
        details = ""
        if ev.get("forecast"):
            details = f" (forecast {ev['forecast']}, prev {ev.get('previous', '?')})"
        lines.append(f"{time_et} ET [{impact}] {title}{details}")
    return lines


def format_raw_briefing(prices: list[dict], headlines: list[str],
                        econ_events: list[str]) -> str:
    """The raw data blob that gets handed to Claude."""
    date_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    lines = [f"Market data for {date_str} (all changes vs prior close):", ""]
    for row in prices:
        sign = "+" if row["pct_change"] >= 0 else ""
        lines.append(f"{row['ticker']}: ${row['close']} ({sign}{row['pct_change']}%)")
    lines.append("")
    lines.append("Today's US economic calendar (data releases and Fed events):")
    lines.extend(econ_events if econ_events else ["(nothing scheduled)"])
    lines.append("")
    lines.append("Headlines from the last 24 hours:")
    lines.extend(headlines if headlines else ["(no macro headlines found)"])
    return "\n".join(lines)


def summarize_with_llm(raw_briefing: str) -> str:
    """Condense the raw briefing using GitHub Models (free tier)."""
    import requests

    system_prompt = (
        "You write a pre-market daily briefing delivered as a Telegram "
        "message. Hard limit: 2400 characters. Style: terse, "
        "information-dense, no fluff, no greetings, no markdown formatting. "
        "Plain text with these ALL-CAPS section headers, in this order, "
        "omitting any section with nothing notable:\n"
        "WATCHLIST - each ticker with price and % change, one per line.\n"
        "TRADING WATCH - today's economic calendar items that can move SPY "
        "and the broad market (Fed/FOMC events, CPI, jobs data, etc.) with "
        "their ET times, plus a one-line take on what to watch for. If the "
        "Fed is speaking or a high-impact release is due, flag it clearly.\n"
        "M&A - mergers, acquisitions, buyouts, and big deals from the "
        "headlines, with the companies and dollar amounts if known.\n"
        "WORLD - geopolitical and world events that matter for markets.\n"
        "MACRO - 2-3 sentences on the other most market-moving news.\n"
        "Use your judgment to skip headlines that don't matter."
    )
    response = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_briefing},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def get_telegram_credentials() -> tuple[str, str]:
    """Read and sanity-check the Telegram secrets, with clear errors."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().removeprefix("bot")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    # Auto-correct swapped secrets: bot tokens contain ":", chat ids are digits.
    if ":" not in token and ":" in chat_id:
        print("note: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID look swapped; "
              "using them in the right order.")
        token, chat_id = chat_id, token

    if ":" not in token:
        sys.exit(
            "error: the TELEGRAM_BOT_TOKEN secret doesn't look like a bot token. "
            "It should be the full string from BotFather, like "
            "'123456789:AAFxYz...', including the colon."
        )
    if not chat_id.lstrip("-").isdigit():
        sys.exit(
            "error: the TELEGRAM_CHAT_ID secret doesn't look like a chat id. "
            "It should be just a number, like 8864014810."
        )
    return token, chat_id


def format_watchlist(prices: list[dict]) -> str:
    lines = []
    for row in prices:
        sign = "+" if row["pct_change"] >= 0 else ""
        lines.append(f"{row['ticker']}: ${row['close']} ({sign}{row['pct_change']}%)")
    return "\n".join(lines)


def get_market_database() -> str:
    return get_or_create_database(MARKET_DB_TITLE, {
        "Name": {"title": {}},
        "Date": {"date": {}},
        "Summary": {"rich_text": {}},
        "Watchlist": {"rich_text": {}},
        "Key": {"rich_text": {}},
    })


def sync_to_notion(prices: list[dict], summary: str, day: str, dry_run: bool) -> None:
    """Write today's market briefing to the Market Daily database."""
    if dry_run and not os.environ.get("NOTION_TOKEN"):
        print("DRY_RUN=1 and no NOTION_TOKEN; would sync market summary to Notion.")
        return

    db_id = get_market_database()
    dedupe_key = f"market-{day}"
    if key_exists(db_id, dedupe_key):
        print(f"Market entry for {day} already in Notion; skipping.")
        return

    title = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    properties = {
        "Name": {"title": [{"text": {"content": title}}]},
        "Date": {"date": {"start": day}},
        "Summary": {"rich_text": rich_text_chunks(summary)},
        "Watchlist": {"rich_text": rich_text_chunks(format_watchlist(prices))},
        "Key": {"rich_text": [{"text": {"content": dedupe_key}}]},
    }
    if dry_run:
        print(f"DRY_RUN: would add Notion market entry for {day}.")
        return

    notion_request("POST", "/pages", {
        "parent": {"database_id": db_id},
        "properties": properties,
    })
    print(f"Synced market summary for {day} to Notion.")


def send_telegram(body: str) -> None:
    import requests

    token, chat_id = get_telegram_credentials()
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": body},
        timeout=30,
    )
    if response.status_code == 404:
        sys.exit(
            "error: Telegram rejected the bot token (404). The "
            "TELEGRAM_BOT_TOKEN secret value is not the token BotFather gave "
            "you. Edit the secret and paste the full token, e.g. "
            "'123456789:AAFxYz...'."
        )
    if response.status_code == 400:
        sys.exit(
            f"error: Telegram rejected the request ({response.text}). "
            "Double-check the TELEGRAM_CHAT_ID secret."
        )
    response.raise_for_status()
    print("Telegram message sent.")


def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"

    tickers = get_watchlist()
    print(f"Fetching prices for {', '.join(tickers)}...")
    prices = fetch_prices(tickers)
    if not prices:
        sys.exit("error: could not fetch any price data")

    print("Fetching headlines...")
    headlines = fetch_headlines()
    print("Fetching economic calendar...")
    econ_events = fetch_econ_calendar()
    print(f"Got {len(prices)} tickers, {len(headlines)} headlines, "
          f"{len(econ_events)} econ events today.")

    raw_briefing = format_raw_briefing(prices, headlines, econ_events)

    if os.environ.get("GITHUB_TOKEN"):
        print(f"Summarizing with {LLM_MODEL} via GitHub Models...")
        try:
            summary = summarize_with_llm(raw_briefing)
        except Exception as exc:
            # Better to deliver the raw data than nothing at all.
            print(f"warning: LLM summarization failed ({exc}); "
                  "sending raw briefing instead.", file=sys.stderr)
            summary = raw_briefing
    else:
        print("No GITHUB_TOKEN set; using raw briefing.")
        summary = raw_briefing

    if len(summary) > MESSAGE_CHAR_LIMIT:
        summary = summary[: MESSAGE_CHAR_LIMIT - 3] + "..."

    print("\n----- summary -----")
    print(summary)
    print(f"----- {len(summary)} chars -----\n")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if os.environ.get("NOTION_TOKEN"):
        sync_to_notion(prices, summary, day, dry_run)
    elif not dry_run:
        print("note: NOTION_TOKEN not set; skipping Notion sync.")

    if dry_run:
        print("DRY_RUN=1, skipping Telegram send.")
    else:
        send_telegram(summary)


if __name__ == "__main__":
    main()
