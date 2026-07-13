"""Daily AI agenda pipeline.

Flow: fetch Outlook calendar + recent inbox (Microsoft Graph)
      -> LLM turns it into a morning plan -> deliver via Telegram.

Runs on a schedule via GitHub Actions (see .github/workflows/daily-agenda.yml).

Privacy note: email subjects/previews are sent to GitHub Models for
summarization, and are never printed to the workflow log (logs only show
counts). The summary itself is only printed in DRY_RUN mode.

Environment variables:
    MS_CLIENT_ID         Azure app registration client id
    MS_REFRESH_TOKEN     Microsoft OAuth refresh token (created by the
                         "Microsoft sign-in" workflow / get_ms_token.py)
    GH_PAT               GitHub personal access token with repo scope, used to
                         keep MS_REFRESH_TOKEN up to date as Microsoft rotates it
    GITHUB_TOKEN         Token for GitHub Models (free LLM inference)
    TELEGRAM_BOT_TOKEN   Bot token from @BotFather
    TELEGRAM_CHAT_ID     Your chat id with the bot
    TIMEZONE             Windows-style timezone name for calendar times
                         (default "Eastern Standard Time")
    LLM_MODEL            GitHub Models model id (default openai/gpt-4o-mini)
    SAMPLE_DATA          Set to "1" to use built-in fake data (no Microsoft
                         account needed; for testing the pipeline)
    DRY_RUN              Set to "1" to print the agenda instead of sending it
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
SCOPES = "offline_access User.Read Mail.Read Calendars.ReadWrite"
# Must match the client used at sign-in time (see get_ms_token.py).
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID") or "14d82eec-204b-4c2f-b7e8-296a70dab67e"

CALENDAR_HOURS_AHEAD = 48
EMAIL_HOURS_BACK = 24
MAX_EMAILS = 40
MESSAGE_CHAR_LIMIT = 2000

LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini"
TIMEZONE = os.environ.get("TIMEZONE") or "Eastern Standard Time"


# ---------------------------------------------------------------- Microsoft

def persist_refresh_token(new_token: str) -> None:
    """Microsoft rotates refresh tokens; store the newest one back as a
    repo secret so the pipeline keeps working indefinitely."""
    if not new_token or new_token == os.environ.get("MS_REFRESH_TOKEN"):
        return
    pat = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not pat or not repo:
        print("note: GH_PAT or GITHUB_REPOSITORY not set; cannot store the "
              "rotated Microsoft token. The pipeline will keep working until "
              "the current token expires (~90 days).", file=sys.stderr)
        return
    try:
        subprocess.run(
            ["gh", "secret", "set", "MS_REFRESH_TOKEN", "--repo", repo],
            input=new_token.encode(),
            env={**os.environ, "GH_TOKEN": pat},
            check=True,
            capture_output=True,
        )
        print("Stored rotated Microsoft refresh token.")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"warning: could not store rotated token ({exc})", file=sys.stderr)


def get_access_token() -> str:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": MS_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": os.environ["MS_REFRESH_TOKEN"],
            "scope": SCOPES,
        },
        timeout=30,
    )
    payload = response.json()
    if "access_token" not in payload:
        if payload.get("error") == "invalid_grant":
            sys.exit(
                "error: the Microsoft sign-in has expired. Re-run the "
                "'Microsoft sign-in' workflow from the Actions tab to fix it."
            )
        sys.exit(f"error: Microsoft token request failed: "
                 f"{payload.get('error_description', response.text)[:300]}")
    persist_refresh_token(payload.get("refresh_token", ""))
    return payload["access_token"]


def graph_get(token: str, url: str, params: dict) -> dict:
    response = requests.get(
        url,
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Prefer": f'outlook.timezone="{TIMEZONE}"',
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def graph_post(token: str, url: str, payload: dict) -> dict:
    response = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": f'outlook.timezone="{TIMEZONE}"',
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_calendar(token: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    data = graph_get(
        token,
        f"{GRAPH}/me/calendarview",
        {
            "startDateTime": now.isoformat(),
            "endDateTime": (now + timedelta(hours=CALENDAR_HOURS_AHEAD)).isoformat(),
            "$select": "subject,start,end,location,isAllDay",
            "$orderby": "start/dateTime",
            "$top": 25,
        },
    )
    events = []
    for item in data.get("value", []):
        events.append({
            "subject": item.get("subject", "(no title)"),
            "start": item["start"]["dateTime"][:16],  # YYYY-MM-DDTHH:MM
            "end": item["end"]["dateTime"][:16],
            "location": (item.get("location") or {}).get("displayName", ""),
            "all_day": item.get("isAllDay", False),
        })
    return events


def fetch_recent_email(token: str) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=EMAIL_HOURS_BACK)
    data = graph_get(
        token,
        f"{GRAPH}/me/messages",
        {
            "$filter": f"receivedDateTime ge {cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$select": "subject,from,receivedDateTime,bodyPreview,importance,isRead",
            "$orderby": "receivedDateTime desc",
            "$top": MAX_EMAILS,
        },
    )
    emails = []
    for item in data.get("value", []):
        sender = (item.get("from") or {}).get("emailAddress", {})
        emails.append({
            "from": sender.get("name") or sender.get("address", "unknown"),
            "subject": item.get("subject", "(no subject)"),
            "preview": (item.get("bodyPreview") or "").replace("\r", " ")
                                                      .replace("\n", " ")[:200],
            "unread": not item.get("isRead", True),
            "important": item.get("importance") == "high",
        })
    return emails


# ------------------------------------------------------------- sample data

SAMPLE_EVENTS = [
    {"subject": "Econ lecture", "start": "2026-07-02T10:00", "end": "2026-07-02T11:30",
     "location": "Hall B", "all_day": False},
    {"subject": "Gym with Jake", "start": "2026-07-02T17:00", "end": "2026-07-02T18:00",
     "location": "", "all_day": False},
]

SAMPLE_EMAILS = [
    {"from": "Prof. Rivera", "subject": "Problem set 4 due Friday",
     "preview": "Reminder that PS4 is due Friday at 5pm. No late submissions.",
     "unread": True, "important": False},
    {"from": "Alpaca", "subject": "Your API key expires in 7 days",
     "preview": "Rotate your key in the dashboard before July 9 to avoid interruption.",
     "unread": True, "important": True},
    {"from": "Sarah Chen", "subject": "Coffee next week?",
     "preview": "Would love to catch up - are you free Tuesday or Wednesday afternoon?",
     "unread": True, "important": False},
]


# --------------------------------------------------------------------- LLM

def format_raw_briefing(events: list[dict], emails: list[dict]) -> str:
    lines = [f"Calendar for the next {CALENDAR_HOURS_AHEAD} hours "
             f"(times are {TIMEZONE}):"]
    if not events:
        lines.append("(no events scheduled)")
    for ev in events:
        when = "all day" if ev["all_day"] else f"{ev['start']} to {ev['end']}"
        loc = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"- {ev['subject']} | {when}{loc}")

    lines.append("")
    lines.append(f"Emails from the last {EMAIL_HOURS_BACK} hours "
                 "(newest first):")
    if not emails:
        lines.append("(no new email)")
    for em in emails:
        flags = ("[UNREAD]" if em["unread"] else "") + \
                ("[IMPORTANT]" if em["important"] else "")
        lines.append(f"- From {em['from']}{flags}: {em['subject']} | {em['preview']}")
    return "\n".join(lines)


def build_agenda_with_llm(raw_briefing: str) -> str:
    system_prompt = (
        "You are a personal chief of staff. From the calendar and inbox data "
        "provided, write a morning agenda as a Telegram message. Hard limit: "
        "1800 characters. Plain text only, no markdown. Structure:\n"
        "TODAY - the day's schedule with times, plus anything tomorrow morning "
        "worth knowing about tonight.\n"
        "NEEDS ACTION - emails that require a reply or contain a deadline or "
        "commitment; be specific about what to do and by when.\n"
        "SHOULD SCHEDULE - things mentioned in email that belong on the "
        "calendar (meetings proposed, deadlines to block time for); suggest a "
        "concrete free time slot that doesn't clash with existing events.\n"
        "Skip newsletters, promotions, and automated noise entirely. If a "
        "section is empty, omit it. Be terse and specific; no filler."
    )
    response = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_briefing},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------- Telegram

def get_telegram_credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().removeprefix("bot")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if ":" not in token and ":" in chat_id:
        token, chat_id = chat_id, token
    if ":" not in token:
        sys.exit("error: TELEGRAM_BOT_TOKEN doesn't look like a bot token.")
    if not chat_id.lstrip("-").isdigit():
        sys.exit("error: TELEGRAM_CHAT_ID doesn't look like a chat id.")
    return token, chat_id


def send_telegram(body: str) -> None:
    token, chat_id = get_telegram_credentials()
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": body},
        timeout=30,
    )
    response.raise_for_status()
    print("Telegram message sent.")


# --------------------------------------------------------------------- main

def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"

    if os.environ.get("SAMPLE_DATA") == "1":
        print("Using built-in sample data (SAMPLE_DATA=1).")
        events, emails = SAMPLE_EVENTS, SAMPLE_EMAILS
    else:
        try:
            print("Signing in to Microsoft...")
            token = get_access_token()
            print("Fetching calendar and inbox...")
            events = fetch_calendar(token)
            emails = fetch_recent_email(token)
        except SystemExit as exc:
            msg = (
                "Your morning AI agenda couldn't run — Microsoft sign-in expired.\n\n"
                "Fix: GitHub → Actions → Microsoft sign-in (run once) → Run workflow. "
                "Open the log, go to https://login.microsoft.com/device, enter the "
                "code within 15 minutes, and sign in with your Outlook account."
            )
            print(f"warning: Microsoft unavailable ({exc})", file=sys.stderr)
            if dry_run:
                print("\n----- agenda (sign-in required) -----")
                print(msg)
                return
            if os.environ.get("TELEGRAM_BOT_TOKEN"):
                send_telegram(msg)
                print("Sent sign-in reminder via Telegram.")
            else:
                sys.exit(str(exc))
            return
        except Exception as exc:
            print(f"warning: Microsoft fetch failed ({exc})", file=sys.stderr)
            events, emails = [], []

    print(f"Got {len(events)} calendar events, {len(emails)} emails.")
    raw_briefing = format_raw_briefing(events, emails)

    print(f"Building agenda with {LLM_MODEL} via GitHub Models...")
    try:
        agenda = build_agenda_with_llm(raw_briefing)
    except Exception as exc:
        print(f"warning: LLM failed ({exc}); sending raw briefing.",
              file=sys.stderr)
        agenda = raw_briefing

    if len(agenda) > MESSAGE_CHAR_LIMIT:
        agenda = agenda[: MESSAGE_CHAR_LIMIT - 3] + "..."

    if dry_run:
        # Only print contents in dry-run; scheduled runs keep email data
        # out of the (potentially public) workflow logs.
        print("\n----- agenda -----")
        print(agenda)
        print(f"----- {len(agenda)} chars -----\n")
        print("DRY_RUN=1, skipping Telegram send.")
    else:
        print(f"Agenda built ({len(agenda)} chars). Sending...")
        send_telegram(agenda)


if __name__ == "__main__":
    main()
