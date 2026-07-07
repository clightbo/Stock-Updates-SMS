"""AI planner sync: Outlook email + calendar -> Notion planner database.

Flow: fetch Outlook calendar + recent inbox (Microsoft Graph)
      -> LLM extracts tasks / deadlines / things-to-schedule from email
      -> upserts everything into a Notion database (the "planner hub")
      -> optional Telegram ping with what was added.

The Notion database is created automatically on the first run (under the
page you point it at) and found by title on later runs. Every item carries
a stable dedupe key, so re-running never creates duplicates.

Runs on a schedule via GitHub Actions (see .github/workflows/planner-sync.yml).

Environment variables:
    NOTION_TOKEN           Notion internal-integration secret (ntn_... / secret_...)
    NOTION_PARENT_PAGE_ID  Page id (or full URL) of the Notion page the
                           planner database should live under. The page must
                           be shared with the integration.
    MS_REFRESH_TOKEN       Microsoft OAuth refresh token (from the
                           "Microsoft sign-in" workflow)
    GH_PAT                 GitHub PAT with repo scope (keeps the Microsoft
                           token fresh; reused from the agenda pipeline)
    GITHUB_TOKEN           Token for GitHub Models (free LLM inference)
    TELEGRAM_BOT_TOKEN     Optional; bot token for the "synced" ping
    TELEGRAM_CHAT_ID       Optional; your chat id with the bot
    LLM_MODEL              GitHub Models model id (default openai/gpt-4o-mini)
    SAMPLE_DATA            "1" = use built-in fake data (no Microsoft needed)
    DRY_RUN                "1" = print what would be added; no Notion/Telegram
                           writes (Notion is still read for dedupe if creds
                           are present)
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from daily_agenda import (
    SAMPLE_EMAILS,
    SAMPLE_EVENTS,
    TIMEZONE,
    fetch_recent_email,
    get_access_token,
    graph_get,
    send_telegram,
)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DB_TITLE = "AI Planner"

CALENDAR_DAYS_AHEAD = 7
LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini"

ITEM_TYPES = ["Task", "Deadline", "Follow-up", "Meeting to schedule", "Event"]


# ---------------------------------------------------------------- Microsoft

def fetch_calendar_week(token: str) -> list[dict]:
    """Like daily_agenda.fetch_calendar but looks a full week ahead,
    so the planner sees everything worth planning around."""
    now = datetime.now(timezone.utc)
    data = graph_get(
        token,
        "https://graph.microsoft.com/v1.0/me/calendarview",
        {
            "startDateTime": now.isoformat(),
            "endDateTime": (now + timedelta(days=CALENDAR_DAYS_AHEAD)).isoformat(),
            "$select": "subject,start,end,location,isAllDay",
            "$orderby": "start/dateTime",
            "$top": 50,
        },
    )
    events = []
    for item in data.get("value", []):
        events.append({
            "subject": item.get("subject", "(no title)"),
            "start": item["start"]["dateTime"][:16],
            "end": item["end"]["dateTime"][:16],
            "location": (item.get("location") or {}).get("displayName", ""),
            "all_day": item.get("isAllDay", False),
        })
    return events


# --------------------------------------------------------------------- LLM

EXTRACT_PROMPT = (
    "You are a planning assistant. From the emails below, extract concrete "
    "planner items: tasks to do, deadlines, meetings someone proposed, and "
    "commitments. Skip newsletters, promotions, receipts, and automated "
    "noise entirely.\n"
    "Today is {today} ({tz}). Existing calendar events (do not duplicate "
    "these): {calendar}.\n\n"
    "Reply with ONLY a JSON array, no prose, no markdown fence. Each element:\n"
    '{{"title": "short actionable title", '
    '"type": "Task" | "Deadline" | "Follow-up" | "Meeting to schedule", '
    '"due": "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" or null, '
    '"notes": "1 sentence of context", '
    '"from": "sender name", "subject": "source email subject"}}\n'
    "If nothing is actionable, reply with []."
)


def extract_items_with_llm(emails: list[dict], calendar_subjects: list[str]) -> list[dict]:
    if not emails:
        return []
    email_lines = "\n".join(
        f"- From {em['from']}"
        + ("[UNREAD]" if em["unread"] else "")
        + ("[IMPORTANT]" if em["important"] else "")
        + f": {em['subject']} | {em['preview']}"
        for em in emails
    )
    system = EXTRACT_PROMPT.format(
        today=datetime.now().strftime("%A %Y-%m-%d"),
        tz=TIMEZONE,
        calendar=", ".join(calendar_subjects) or "(none)",
    )
    response = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 1500,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": email_lines},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return parse_items(response.json()["choices"][0]["message"]["content"])


def parse_items(raw: str) -> list[dict]:
    """Parse the LLM reply into planner items, tolerating markdown fences
    and dropping anything malformed rather than failing the whole run."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    items = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("title"):
            continue
        items.append({
            "title": str(entry["title"])[:200],
            "type": entry.get("type") if entry.get("type") in ITEM_TYPES else "Task",
            "due": entry.get("due") or None,
            "notes": str(entry.get("notes") or "")[:500],
            "from": str(entry.get("from") or "")[:100],
            "subject": str(entry.get("subject") or "")[:200],
        })
    return items


# ------------------------------------------------------------------ Notion

def notion_request(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(
        method,
        f"{NOTION_API}{path}",
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        sys.exit(f"error: Notion API {method} {path} failed "
                 f"({response.status_code}): {response.text[:300]}")
    return response.json()


def normalize_notion_id(value: str) -> str:
    """Accept a bare id, a dashed id, or a full Notion URL.

    In a Notion URL the id is the trailing 32 hex chars of the last path
    segment, so take the END of the last long-enough hex run (the page
    title itself may end in hex-looking characters like "...Hub").
    """
    compact = value.split("?")[0].replace("-", "")
    runs = re.findall(r"[0-9a-f]{32,}", compact, flags=re.IGNORECASE)
    if not runs:
        sys.exit("error: NOTION_PARENT_PAGE_ID doesn't look like a Notion "
                 "page id or URL.")
    return runs[-1][-32:]


def find_planner_database() -> str | None:
    data = notion_request("POST", "/search", {
        "query": DB_TITLE,
        "filter": {"value": "database", "property": "object"},
        "page_size": 20,
    })
    for result in data.get("results", []):
        title = "".join(t.get("plain_text", "") for t in result.get("title", []))
        if title.strip() == DB_TITLE:
            return result["id"]
    return None


def create_planner_database(parent_page_id: str) -> str:
    print(f'Creating Notion database "{DB_TITLE}"...')
    data = notion_request("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": DB_TITLE}}],
        "properties": {
            "Name": {"title": {}},
            "Type": {"select": {"options": [
                {"name": "Task", "color": "blue"},
                {"name": "Deadline", "color": "red"},
                {"name": "Follow-up", "color": "yellow"},
                {"name": "Meeting to schedule", "color": "purple"},
                {"name": "Event", "color": "green"},
            ]}},
            "Status": {"select": {"options": [
                {"name": "Inbox", "color": "gray"},
                {"name": "Planned", "color": "yellow"},
                {"name": "Done", "color": "green"},
            ]}},
            "Due": {"date": {}},
            "Source": {"select": {"options": [
                {"name": "Email", "color": "orange"},
                {"name": "Calendar", "color": "green"},
            ]}},
            "From": {"rich_text": {}},
            "Notes": {"rich_text": {}},
            "Key": {"rich_text": {}},
        },
    })
    return data["id"]


def get_planner_database() -> str:
    db_id = find_planner_database()
    if db_id:
        return db_id
    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not parent:
        sys.exit(f'error: no "{DB_TITLE}" database found and '
                 "NOTION_PARENT_PAGE_ID is not set, so I can't create one. "
                 "Add the secret (see README) and re-run.")
    return create_planner_database(normalize_notion_id(parent))


def key_exists(db_id: str, key: str) -> bool:
    data = notion_request("POST", f"/databases/{db_id}/query", {
        "filter": {"property": "Key", "rich_text": {"equals": key}},
        "page_size": 1,
    })
    return bool(data.get("results"))


def add_page(db_id: str, item: dict) -> None:
    properties = {
        "Name": {"title": [{"text": {"content": item["title"]}}]},
        "Type": {"select": {"name": item["type"]}},
        "Status": {"select": {"name": "Inbox"}},
        "Source": {"select": {"name": item["source"]}},
        "Key": {"rich_text": [{"text": {"content": item["key"]}}]},
    }
    if item.get("due"):
        properties["Due"] = {"date": {"start": item["due"]}}
    if item.get("from"):
        properties["From"] = {"rich_text": [{"text": {"content": item["from"]}}]}
    if item.get("notes"):
        properties["Notes"] = {"rich_text": [{"text": {"content": item["notes"]}}]}
    notion_request("POST", "/pages", {
        "parent": {"database_id": db_id},
        "properties": properties,
    })


# ------------------------------------------------------------------- items

def dedupe_key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def build_items(events: list[dict], emails: list[dict]) -> list[dict]:
    items = []
    for ev in events:
        due = ev["start"][:10] if ev["all_day"] else ev["start"]
        notes = " - ".join(p for p in [
            "" if ev["all_day"] else f"until {ev['end'][11:16]}",
            ev["location"],
        ] if p)
        items.append({
            "title": ev["subject"],
            "type": "Event",
            "due": due,
            "notes": notes,
            "from": "",
            "source": "Calendar",
            "key": dedupe_key("cal", ev["subject"], ev["start"]),
        })

    try:
        extracted = extract_items_with_llm(
            emails, [ev["subject"] for ev in events])
    except Exception as exc:
        print(f"warning: LLM extraction failed ({exc}); syncing calendar "
              "events only this run.", file=sys.stderr)
        extracted = []
    for it in extracted:
        items.append({
            "title": it["title"],
            "type": it["type"],
            "due": it["due"],
            "notes": it["notes"],
            "from": it["from"],
            "source": "Email",
            "key": dedupe_key("em", it["from"], it["subject"], it["title"]),
        })
    return items


# --------------------------------------------------------------------- main

def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"

    if not os.environ.get("NOTION_TOKEN") and not dry_run:
        sys.exit(
            "error: the NOTION_TOKEN secret is missing, so there is nowhere "
            "to sync to yet. Follow the 'Notion planner sync' setup in the "
            "README (create a Notion integration, then add NOTION_TOKEN and "
            "NOTION_PARENT_PAGE_ID as repository secrets) and re-run."
        )

    if os.environ.get("SAMPLE_DATA") == "1":
        print("Using built-in sample data (SAMPLE_DATA=1).")
        events, emails = SAMPLE_EVENTS, SAMPLE_EMAILS
    else:
        print("Signing in to Microsoft...")
        token = get_access_token()
        print("Fetching calendar and inbox...")
        events = fetch_calendar_week(token)
        emails = fetch_recent_email(token)
    print(f"Got {len(events)} calendar events, {len(emails)} emails.")

    items = build_items(events, emails)
    print(f"{len(items)} candidate planner items.")

    if dry_run and not os.environ.get("NOTION_TOKEN"):
        print("\nDRY_RUN=1 and no NOTION_TOKEN; items that would sync:")
        for item in items:
            due = f" (due {item['due']})" if item["due"] else ""
            print(f"- [{item['type']}] {item['title']}{due}")
        return

    db_id = get_planner_database()
    print(f"Using Notion database {db_id}.")

    added = []
    for item in items:
        if key_exists(db_id, item["key"]):
            continue
        if dry_run:
            print(f"DRY_RUN: would add [{item['type']}] {item['title']}")
        else:
            add_page(db_id, item)
            print(f"Added [{item['type']}] {item['title']}")
        added.append(item)
    print(f"Done: {len(added)} new, {len(items) - len(added)} already in Notion.")

    if added and not dry_run and os.environ.get("TELEGRAM_BOT_TOKEN"):
        lines = [f"Planner synced: {len(added)} new item(s) in Notion"]
        for item in added[:10]:
            due = f" - due {item['due']}" if item["due"] else ""
            lines.append(f"- {item['title']}{due}")
        if len(added) > 10:
            lines.append(f"...and {len(added) - 10} more")
        try:
            send_telegram("\n".join(lines))
        except (Exception, SystemExit) as exc:
            print(f"warning: Telegram ping failed ({exc})", file=sys.stderr)


if __name__ == "__main__":
    main()
