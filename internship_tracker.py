"""Finance internship tracker — Dallas / NYC S&T, AM, equity research, IB + discovery.

Flow: poll Greenhouse + Workday + curated program pages (+ optional Outlook inbox)
      -> filter for Dallas finance roles and sophomore discovery programs
      -> upsert into a Notion "Internship Tracker" database
      -> Telegram alert for new postings and upcoming deadlines

Runs on a schedule via GitHub Actions (see .github/workflows/internship-tracker.yml).

Environment variables:
    NETWORKING_TELEGRAM_BOT_TOKEN  Separate bot from market summary (see README)
    NETWORKING_TELEGRAM_CHAT_ID    Chat id with networking bot (can match main chat id)
    NETWORKING_BOT_NAME            Display name in messages (default Campus Coach)
    GITHUB_TOKEN                   GitHub Models for outreach drafts (auto in Actions)
    NOTION_TOKEN             Notion integration secret (optional but recommended)
    NOTION_PARENT_PAGE_ID    Notion hub page for the tracker database
    MS_REFRESH_TOKEN         Microsoft OAuth refresh token (optional; email scan)
    GH_PAT                   GitHub PAT with repo scope (keeps MS token fresh)
    SKIP_MICROSOFT           "1" = skip recruiting-email scan
    INTERNSHIP_LOCATIONS     Comma-separated location keywords (default Dallas,DFW,Texas)
    INTERNSHIP_DIVISIONS       Comma-separated divisions (default S&T,AM,IB)
    INTERNSHIP_CLASS_YEARS   Comma-separated class years (default Sophomore,Discovery)
    DRY_RUN                  "1" = print results; no Notion/Telegram writes
    SAMPLE_DATA              "1" = use built-in sample recruiting emails
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from daily_agenda import (
    fetch_recent_email,
    get_access_token,
)
from networking_coach import (
    build_networking_messages,
    get_networking_telegram_credentials,
    send_networking_telegram,
)
from internship_sources import (
    JobPosting,
    discovery_season_active,
    fetch_all_postings,
    filter_postings,
    upcoming_early_alerts,
)
from notion_client import get_or_create_database, key_exists, rich_text_chunks

DB_TITLE = "Internship Tracker"
MESSAGE_CHAR_LIMIT = 3500

SAMPLE_RECRUITING_EMAILS = [
    {
        "from": "Morgan Stanley Campus Recruiting",
        "subject": "2028 Investment Banking Summer Analyst — Dallas & NYC",
        "preview": (
            "Applications are open for the 2028 Summer Analyst program in "
            "Investment Banking. Dallas and New York offices. Apply at "
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
            "Discovery externship. Investment Banking division. "
            "Dallas and New York participating."
        ),
        "unread": True,
        "important": False,
    },
    {
        "from": "JPMorgan Chase",
        "subject": "Hispanic & Latino Markets Fellowship — apply now",
        "preview": (
            "Applications open for the Hispanic and Latino Markets Fellowship. "
            "Global Markets division. Dallas and New York sessions. "
            "Sophomores and rising sophomores encouraged."
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
            {"name": "Freshman", "color": "purple"},
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
            {"name": "Early alert", "color": "purple"},
        ]}},
        "Notes": {"rich_text": {}},
        "Key": {"rich_text": {}},
    }


def get_tracker_database() -> str:
    return get_or_create_database(DB_TITLE, db_properties())


def add_posting(db_id: str, posting: JobPosting, *, key: str | None = None) -> None:
    from notion_client import notion_request

    row_key = key or posting.dedupe_key()
    properties: dict = {
        "Name": {"title": [{"text": {"content": posting.title[:200]}}]},
        "Status": {"select": {"name": "New"}},
        "Key": {"rich_text": [{"text": {"content": row_key}}]},
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


def format_early_alert_message(alerts: list[JobPosting]) -> str:
    today = datetime.now().strftime("%A, %b %d, %Y").replace(" 0", " ")
    lines = [
        f"HEADS UP — {today}",
        "Programs opening in ~1–2 months. Time to network before apps drop.",
        "",
    ]
    for p in alerts[:10]:
        div = f" ({p.division})" if p.division else ""
        win = f" [{p.alert_window} out]" if p.alert_window else ""
        lines.append(f"- {p.firm}{div}{win}")
        lines.append(f"  {p.title}")
        if p.notes:
            # Keep tip line short for Telegram.
            tip = p.notes.split("Tip: ", 1)[-1] if "Tip: " in p.notes else p.notes
            lines.append(f"  {tip[:220]}")
        if p.url:
            lines.append(f"  {p.url}")
        lines.append("")
    if len(alerts) > 10:
        lines.append(f"...and {len(alerts) - 10} more in Notion")
    body = "\n".join(lines).strip()
    if len(body) > MESSAGE_CHAR_LIMIT:
        body = body[: MESSAGE_CHAR_LIMIT - 3] + "..."
    return body


def format_telegram_message(
    new_postings: list[JobPosting],
    *,
    total_scanned: int,
    season_note: str,
) -> str:
    today = datetime.now().strftime("%A, %b %d, %Y").replace(" 0", " ")
    lines = [f"INTERNSHIP WATCH — {today}", season_note, ""]

    if not new_postings:
        lines.append(f"No new roles today ({total_scanned} scanned).")
        lines.append("Markets / discovery programs usually open late Aug–Sep.")
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
            "DISCOVERY SEASON — markets rotations, S&T, AM, equity research & IB "
            "programs opening (Aug–Nov). Dallas, NYC, and national fellowships."
        )
    return (
        "Daily scan — watching markets rotations, S&T, AM, equity research & IB "
        "in Dallas & NYC, plus 1–2 month early alerts."
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
    locations = parse_csv_env(
        "INTERNSHIP_LOCATIONS",
        ("dallas", "dfw", "texas", "lubbock", "irving", "plano", "fort worth",
         "new york", "nyc", "manhattan"),
    )
    divisions = parse_csv_env("INTERNSHIP_DIVISIONS", ("S&T", "AM", "IB"))
    class_years = parse_csv_env(
        "INTERNSHIP_CLASS_YEARS", ("Freshman", "Sophomore", "Discovery"),
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
    print(f"{len(matched)} postings match filters.")

    # --- Early heads-up alerts (1–2 months before programs open) ---
    early_candidates = upcoming_early_alerts()
    print(f"{len(early_candidates)} program(s) in the 1–2 month early-alert window.")

    if dry_run and not os.environ.get("NOTION_TOKEN"):
        print("\nDRY_RUN matches:")
        for p in matched[:25]:
            print(f"- [{p.source}] {p.firm} | {p.title} | {p.location}")
            if p.url:
                print(f"  {p.url}")
        if early_candidates:
            print("\nDRY_RUN early alerts:")
            for posting, key in early_candidates[:10]:
                print(f"- [{posting.alert_window}] {posting.firm} | {posting.title}")
                print(f"  {posting.notes[:180]}")
        msg = format_telegram_message(
            matched[:5], total_scanned=len(raw), season_note=season_banner(),
        )
        print("\n----- Telegram preview -----")
        print(msg)
        if early_candidates:
            print("\n----- Early alert preview -----")
            print(format_early_alert_message([p for p, _ in early_candidates[:5]]))
        previews = build_networking_messages(
            [p for p, _ in early_candidates[:2]],
            matched[:2],
        )
        if previews:
            print("\n----- Campus Coach preview -----")
            for msg in previews:
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

    new_early: list[JobPosting] = []
    for posting, alert_key in early_candidates:
        if key_exists(db_id, alert_key):
            continue
        if dry_run:
            print(f"DRY_RUN: would alert [{posting.alert_window}] "
                  f"{posting.firm} — {posting.title}")
        else:
            add_posting(db_id, posting, key=alert_key)
            print(f"Early alert logged [{posting.alert_window}] "
                  f"{posting.firm} — {posting.title}")
        new_early.append(posting)

    print(f"Done: {len(new_postings)} new roles, {len(new_early)} new early alerts, "
          f"{len(matched) - len(new_postings)} roles already tracked.")

    token, _ = get_networking_telegram_credentials()
    if not token:
        if dry_run:
            print("No NETWORKING_TELEGRAM_BOT_TOKEN; skipping send.")
        else:
            print("note: set NETWORKING_TELEGRAM_BOT_TOKEN for a separate recruiting "
                  "bot (see README). Skipping Telegram.", file=sys.stderr)
        return

    messages: list[str] = []

    if new_early:
        messages.append(format_early_alert_message(new_early))

    should_send_roles = bool(new_postings) or (
        discovery_season_active() and matched and dry_run
    )
    if should_send_roles and new_postings:
        messages.append(format_telegram_message(
            new_postings,
            total_scanned=len(raw),
            season_note=season_banner(),
        ))
    elif should_send_roles and dry_run and matched:
        messages.append(format_telegram_message(
            matched[:5],
            total_scanned=len(raw),
            season_note=season_banner(),
        ))

    # Networking coach drafts (copy-paste LinkedIn / email) — separate messages.
    coach_messages = build_networking_messages(new_early, new_postings)

    if not messages and not coach_messages:
        print("No new postings or early alerts; skipping Telegram.")
        return

    if dry_run:
        print("\n----- Networking bot preview -----")
        for msg in messages:
            print(msg)
            print()
        for msg in coach_messages:
            print(msg)
            print()
        print("----- end preview -----")
        return

    for msg in messages:
        send_networking_telegram(msg)
    for msg in coach_messages:
        send_networking_telegram(msg)


if __name__ == "__main__":
    main()
