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
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import yfinance as yf

DEFAULT_WATCHLIST = ["SPY", "QQQ", "DIA", "AAPL", "NVDA", "MSFT"]

RSS_FEEDS = {
    "CNBC Markets": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Economy": "https://www.cnbc.com/id/20910255/device/rss/rss.html",
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

# Headlines matching any of these are treated as macro-relevant. Claude does the
# final judgment call on what's notable; this filter just keeps token usage down.
MACRO_KEYWORDS = [
    "fed", "fomc", "powell", "rate", "inflation", "cpi", "ppi", "pce",
    "jobs", "payroll", "unemployment", "gdp", "treasury", "yield", "bond",
    "tariff", "trade", "oil", "opec", "recession", "stimulus", "earnings",
    "market", "stocks", "s&p", "nasdaq", "dow", "rally", "selloff", "crash",
    "dollar", "china", "ecb", "housing", "retail sales", "consumer",
]

MAX_HEADLINES = 25
MESSAGE_CHAR_LIMIT = 1200  # Telegram allows 4096; keep it skimmable

# Free inference via GitHub Models (https://models.github.ai). Any model id
# from the catalog works, e.g. "openai/gpt-4o" or "meta/llama-3.3-70b-instruct".
LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini"


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


def format_raw_briefing(prices: list[dict], headlines: list[str]) -> str:
    """The raw data blob that gets handed to Claude."""
    date_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    lines = [f"Market data for {date_str} (all changes vs prior close):", ""]
    for row in prices:
        sign = "+" if row["pct_change"] >= 0 else ""
        lines.append(f"{row['ticker']}: ${row['close']} ({sign}{row['pct_change']}%)")
    lines.append("")
    lines.append("Headlines from the last 24 hours:")
    lines.extend(headlines if headlines else ["(no macro headlines found)"])
    return "\n".join(lines)


def summarize_with_llm(raw_briefing: str) -> str:
    """Condense the raw briefing using GitHub Models (free tier)."""
    import requests

    system_prompt = (
        "You write a daily market summary delivered as a Telegram message. "
        "Hard limit: 1000 characters. Style: terse, information-dense, "
        "no fluff, no greetings, no markdown. Start with the watchlist moves "
        "(ticker, % change, one per line), then 3-4 sentences on the most "
        "market-moving macro news. Use your judgment to skip headlines that "
        "don't matter. Plain text only."
    )
    response = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 400,
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
    print(f"Got {len(prices)} tickers, {len(headlines)} headlines.")

    raw_briefing = format_raw_briefing(prices, headlines)

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

    if dry_run:
        print("DRY_RUN=1, skipping Telegram send.")
    else:
        send_telegram(summary)


if __name__ == "__main__":
    main()
