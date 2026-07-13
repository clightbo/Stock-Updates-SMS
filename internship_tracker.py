"""Finance internship tracker — Dallas AM / S&T / IB + sophomore discovery.

Flow: poll Greenhouse + Workday + curated program pages (+ optional Outlook inbox)
      -> filter for Dallas finance roles and sophomore discovery programs
      -> upsert into a Notion "Internship Tracker" database
      -> Telegram alert for new postings and upcoming deadlines

Runs on a schedule via GitHub Actions (see .github/workflows/internship-tracker.yml).

Environment variables:
    TELEGRAM_BOT_TOKEN       Bot token from @BotFather
    TELEGRAM_CHAT_ID         Your chat id with the bot
    NOTION_TOKEN             Notion integration secret (optional but recommended)
    NOTION_PARENT_PAGE_ID    Notion hub page for the tracker database
    MS_REFRESH_TOKEN         Microsoft OAuth refresh token (optional; email scan)
    GH_PAT                   GitHub PAT with repo scope (keeps MS token fresh)
    SKIP_MICROSOFT           "1" = skip recruiting-email scan
    INTERNSHIP_LOCATIONS     Comma-separated location keywords (default Dallas,DFW,Texas)
    INTERNSHIP_DIVISIONS       Comma-separated divisions (default AM,S&T,IB)
    INTERNSHIP_CLASS_YEARS   Comma-separated class years (default Sophomore,Discovery)
    DRY_RUN                  "1" = print results; no Notion/Telegram writes
    SAMPLE_DATA              "1" = use built-in sample recruiting emails
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from daily_agenda import (
    SAMPLE_EMAILS,
    fetch_recent_email,
    get_access_token,
    send_telegram,
)
from internship_sources import (
    JobPosting,
    discovery_season_active,
    fetch_all_postings,
    filter_postings,
)
from notion_client import get_or_create_database, key_exists, rich_text_chunks

DB_TITLE = "Internship Tracker"
MESSAGE_CHAR_LIMIT = 3500

SAMPLE_RECRUITING_EMAILS = [
    {
        "from": "Morgan Stanley Campus Recruiting",
        "subject": "2027 Global Markets Summer Analyst — Dallas now open",
        "preview": (
            "Applications are open for the 2027 Summer Analyst program in "
            "Dallas, TX (Global Markets). Apply at "
            "https://ms.wd5.myworkdayjobs.com/en-US/External/job/Dallas"
        ),
        "unread": True,
        "important": True,
    },
    {
        "from": "Goldman Sachs Recruiting",
        "subject": "Sophomore Discovery Program — applications open",
        "preview": (
            "We are now accepting applications for the 2026 Sophomore "
            "Discovery externship. Multiple divisions including Investment "
            "Banking and Asset Management. Dallas office participating."
        ),
        "unread": True,
        "important": False,
    },
    {
        "from": "JPMorgan Chase",
        "subject": "Winning Women in Finance — register for Dallas insight day",
        "preview": (
            "Register now for the Winning Women in Finance early insight "
            "program. Dallas session September 12. Sophomores encouraged."
        ),
        "unread": True,
        "important": False,
    },
]


def parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def db_properties() -> dict:
    return {
        "Name": {"title": {}},
        "Firm": {"select": {"options": []}},
        "Division": {"select": {"options": [
            {"name": "AM", "color": "blue"},
            {"name": "S&T", "color": "green"},
            {"name": "IB", "color": "purple"},
            {"name": "Discovery", "color": "yellow"},
        ]}},
        "Location": {"rich_text": {}},
        "Class Year": {"select": {"options": [
            {"name": "Sophomore", "color": "orange"},
            {"name": "Junior", "color": "pink"},
            {"name": "Discovery", "color": "yellow"},
        ]}},
        "Program Type": {"select": {"options": [
            {"name": "Discovery", "color": "yellow"},
            {"name": "Summer Analyst", "color": "blue"},
            {"name": "Internship", "color": "green"},
            {"name": "Off-Cycle", "color": "gray"},
            {"name": "Program", "color": "brown"},
        ]}},
        "Status": {"select": {"options": [
            {"name": "New", "color": "red"},
            {"name": "Applied", "color": "yellow"},
            {"name": "OA", "color": "orange"},
            {"name": "Interview", "color": "blue"},
            {"name": "Offer", "color": "green"},
            {"name": "Closed", "color": "gray"},
        ]}},
        "Posted": {"date": {}},
        "URL": {"url": {}},
        "Source": {"select": {"options": [
            {"name": "Greenhouse", "color": "green"},
            {"name": "Workday", "color": "blue"},
            {"name": "Program watch", "color": "yellow"},
            {"name": "Email", "color": "orange"},
        ]}},
        "Notes": {"rich_text": {}},
        "Key": {"rich_text": {}},
    }


def get_tracker_database() -> str:
    return get_or_create_database(DB_TITLE, db_properties())


def add_posting(db_id: str, posting: JobPosting) -> None:
    from notion_client import notion_request

    properties: dict = {
        "Name": {"title": [{"text": {"content": posting.title[:200]}}]},
        "Status": {"select": {"name": "New"}},
        "Key": {"rich_text": [{"text": {"content": posting.dedupe_key()}}]},
    }
    if posting.firm:
        properties["Firm"] = {"select": {"name": posting.firm[:100]}}
    if posting.division:
        properties["Division"] = {"select": {"name": posting.division[:100]}}
    if posting.location:
        properties["Location"] = {
            "rich_text": rich_text_chunks(posting.location[:500]),
        }
    if posting.class_year:
        properties["Class Year"] = {"select": {"name": posting.class_year}}
    if posting.program_type:
        properties["Program Type"] = {"select": {"name": posting.program_type}}
    if posting.url:
        properties["URL"] = {"url": posting.url}
    if posting.source:
        properties["Source"] = {"select": {"name": posting.source}}
    if posting.posted:
        date_str = posting.posted[:10]
        if len(date_str) == 10 and date_str[4] == "-":
            properties["Posted"] = {"date": {"start": date_str}}
    if posting.notes:
        properties["Notes"] = {"rich_text": rich_text_chunks(posting.notes[:1900])}

    notion_request("POST", "/pages", {
        "parent": {"database_id": db_id},
        "properties": properties,
    })


def format_telegram_message(
    new_postings: list[JobPosting],
    *,
    total_scanned: int,
    season_note: str,
) -> str:
    today = datetime.now().strftime("%A, %b %d, %Y").replace(" 0", " ")
    lines = [f"INTERNSHIP WATCH — {today}", season_note, ""]

    if not new_postings:
        lines.append(f"No new Dallas finance roles today ({total_scanned} scanned).")
        lines.append("Sophomore discovery programs usually open late Aug–Sep.")
        return "\n".join(lines)

    lines.append(f"NEW ({len(new_postings)})")
    for p in new_postings[:12]:
        div = f" {p.division}" if p.division else ""
        loc = f" | {p.location}" if p.location else ""
        year = f" | {p.class_year}" if p.class_year else ""
        lines.append(f"- {p.firm}{div}{year}{loc}")
        lines.append(f"  {p.title}")
        if p.url:
            lines.append(f"  {p.url}")
    if len(new_postings) > 12:
        lines.append(f"...and {len(new_postings) - 12} more in Notion")

    body = "\n".join(lines)
    if len(body) > MESSAGE_CHAR_LIMIT:
        body = body[: MESSAGE_CHAR_LIMIT - 3] + "..."
    return body


def season_banner() -> str:
    if discovery_season_active():
        return (
            "DISCOVERY SEASON — watching sophomore / early insight programs "
            "(Aug–Nov daily scan)."
        )
    return (
        "Off-season scan — still watching boards; expect a surge of sophomore "
        "programs in early fall."
    )


def fetch_recruiting_emails() -> list[dict]:
    if os.environ.get("SAMPLE_DATA") == "1":
        print("Using sample recruiting emails (SAMPLE_DATA=1).")
        return SAMPLE_RECRUITING_EMAILS
    if os.environ.get("SKIP_MICROSOFT") == "1":
        print("Skipping Microsoft email scan (SKIP_MICROSOFT=1).")
        return []
    try:
        print("Scanning Outlook for recruiting emails...")
        token = get_access_token()
        return fetch_recent_email(token)
    except (SystemExit, Exception) as exc:
        print(f"warning: email scan unavailable ({exc}); continuing without it.",
              file=sys.stderr)
        return []


def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"
    locations = parse_csv_env("INTERNSHIP_LOCATIONS", ("dallas", "dfw", "texas"))
    divisions = parse_csv_env("INTERNSHIP_DIVISIONS", ("AM", "S&T", "IB"))
    class_years = parse_csv_env(
        "INTERNSHIP_CLASS_YEARS", ("Sophomore", "Discovery"),
    )

    emails = fetch_recruiting_emails()
    print("Fetching internship postings from job boards and program pages...")
    raw = fetch_all_postings(emails)
    print(f"Fetched {len(raw)} raw postings.")

    matched = filter_postings(
        raw,
        locations=locations,
        divisions=divisions,
        class_years=class_years,
    )
    print(f"{len(matched)} postings match Dallas / {','.join(divisions)} / "
          f"{','.join(class_years)} filters.")

    if dry_run and not os.environ.get("NOTION_TOKEN"):
        print("\nDRY_RUN matches:")
        for p in matched[:25]:
            print(f"- [{p.source}] {p.firm} | {p.title} | {p.location}")
            if p.url:
                print(f"  {p.url}")
        msg = format_telegram_message(
            matched[:5], total_scanned=len(raw), season_note=season_banner(),
        )
        print("\n----- Telegram preview -----")
        print(msg)
        print("----- end preview -----")
        return

    if not os.environ.get("NOTION_TOKEN") and not dry_run:
        sys.exit(
            "error: NOTION_TOKEN is required for tracking. Add it per the "
            "README (Notion planner setup) and re-run."
        )

    db_id = get_tracker_database()
    print(f"Using Notion database {db_id}.")

    new_postings: list[JobPosting] = []
    for posting in matched:
        key = posting.dedupe_key()
        if key_exists(db_id, key):
            continue
        if dry_run:
            print(f"DRY_RUN: would add {posting.firm} — {posting.title}")
        else:
            add_posting(db_id, posting)
            print(f"Added {posting.firm} — {posting.title}")
        new_postings.append(posting)

    print(f"Done: {len(new_postings)} new, "
          f"{len(matched) - len(new_postings)} already tracked.")

    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        if dry_run:
            print("No TELEGRAM_BOT_TOKEN; skipping send.")
        return

    # During discovery season, ping even when nothing new (weekly reminder
    # is handled by workflow schedule; here we only ping on new finds
    # or first run of the day with matches).
    should_send = bool(new_postings) or (
        discovery_season_active() and matched and dry_run
    )
    if not should_send and not dry_run:
        print("No new postings; skipping Telegram.")
        return

    message = format_telegram_message(
        new_postings if new_postings else matched[:5],
        total_scanned=len(raw),
        season_note=season_banner(),
    )

    if dry_run:
        print("\n----- Telegram preview -----")
        print(message)
        print("----- end preview -----")
    else:
        send_telegram(message)


if __name__ == "__main__":
    main()
