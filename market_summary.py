"""Daily market summary pipeline.

Flow: fetch watchlist prices (yfinance) -> fetch macro headlines (RSS)
      -> condense with Claude -> deliver via Twilio SMS.

Runs on a schedule via GitHub Actions (see .github/workflows/daily-summary.yml).

Environment variables:
    ANTHROPIC_API_KEY   Claude API key (required unless DRY_RUN=1)
    TWILIO_ACCOUNT_SID  Twilio account SID (required unless DRY_RUN=1)
    TWILIO_AUTH_TOKEN   Twilio auth token (required unless DRY_RUN=1)
    TWILIO_FROM_NUMBER  Twilio phone number that sends the text, e.g. +15551234567
    SMS_TO_NUMBER       Your phone number, e.g. +15559876543
    WATCHLIST           Comma-separated tickers (optional, defaults below)
    DRY_RUN             Set to "1" to print the summary instead of texting it,
                        and to fall back to a plain-text summary if no Claude key.
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
SMS_CHAR_LIMIT = 640  # ~4 SMS segments; Twilio concatenates automatically

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")


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


def summarize_with_claude(raw_briefing: str) -> str:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system=(
            "You write a daily market summary delivered as a single SMS text "
            "message. Hard limit: 600 characters. Style: terse, information-dense, "
            "no fluff, no greetings, no markdown. Start with the watchlist moves "
            "(ticker, % change), then 2-3 sentences on the most market-moving "
            "macro news. Use your judgment to skip headlines that don't matter. "
            "Plain text only."
        ),
        messages=[{"role": "user", "content": raw_briefing}],
    )
    return response.content[0].text.strip()


def send_sms(body: str) -> None:
    from twilio.rest import Client

    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )
    message = client.messages.create(
        body=body,
        from_=os.environ["TWILIO_FROM_NUMBER"],
        to=os.environ["SMS_TO_NUMBER"],
    )
    print(f"SMS sent, sid={message.sid}")


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

    if os.environ.get("ANTHROPIC_API_KEY"):
        print(f"Summarizing with {CLAUDE_MODEL}...")
        summary = summarize_with_claude(raw_briefing)
    elif dry_run:
        print("No ANTHROPIC_API_KEY set; using raw briefing (dry run only).")
        summary = raw_briefing
    else:
        sys.exit("error: ANTHROPIC_API_KEY is not set")

    if len(summary) > SMS_CHAR_LIMIT:
        summary = summary[: SMS_CHAR_LIMIT - 3] + "..."

    print("\n----- summary -----")
    print(summary)
    print(f"----- {len(summary)} chars -----\n")

    if dry_run:
        print("DRY_RUN=1, skipping SMS.")
    else:
        send_sms(summary)


if __name__ == "__main__":
    main()
