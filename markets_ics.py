"""Build and publish a Google-Calendar-compatible markets ICS feed.

Why: Notion Calendar for lightbourncal@gmail.com is driven by Google, not
Outlook. A public ICS subscription on the calendar-feed branch shows up in
Google Calendar → Notion Calendar automatically.

Environment variables:
    GITHUB_TOKEN / GH_PAT   Token that can push the calendar-feed branch
    GITHUB_REPOSITORY       owner/repo (set automatically in Actions)
    DRY_RUN                 "1" = write markets.ics locally only, no push
    WATCHLIST               Optional tickers for earnings
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from financial_calendar import DEFAULT_DAYS_AHEAD, fetch_financial_announcements

FEED_BRANCH = "calendar-feed"
FEED_FILE = "markets.ics"
CAL_NAME = "Markets (Fed / Macro / Earnings)"


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets (ASCII-safe for our content)."""
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(chunks)


def _parse_start(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _ics_local(dt: datetime) -> str:
    """Floating local time with TZID=America/New_York (Google-friendly)."""
    if dt.tzinfo is not None:
        # Convert to US/Eastern wall clock for the feed.
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    return dt.strftime("%Y%m%dT%H%M%S")


def _uid(title: str, start: str) -> str:
    digest = hashlib.sha1(f"{title}|{start}".encode()).hexdigest()[:16]
    return f"markets-{digest}@personal-automation-hub"


def build_ics(events: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//personal-automation-hub//Markets//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(CAL_NAME)}",
        "X-WR-TIMEZONE:America/New_York",
        "BEGIN:VTIMEZONE",
        "TZID:America/New_York",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "TZNAME:EDT",
        "DTSTART:20260308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "TZNAME:EST",
        "DTSTART:20261101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for ev in events:
        start = _parse_start(ev["start"])
        end = _parse_start(ev.get("end") or ev["start"])
        summary = ev["title"]
        desc = ev.get("notes") or ""
        impact = ev.get("impact") or ""
        if impact:
            desc = f"Impact: {impact}. {desc}".strip()
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_uid(ev['title'], ev['start'])}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID=America/New_York:{_ics_local(start)}",
            f"DTEND;TZID=America/New_York:{_ics_local(end)}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(desc)}",
            "LOCATION:Markets",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def publish_ics(ics_text: str) -> str:
    """Push markets.ics to the calendar-feed branch; return the public URL."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = (os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not repo or not token:
        sys.exit("error: GITHUB_REPOSITORY and GITHUB_TOKEN/GH_PAT required to publish ICS")

    owner_repo = repo
    remote = f"https://x-access-token:{token}@github.com/{owner_repo}.git"
    url = (
        f"https://raw.githubusercontent.com/{owner_repo}/"
        f"{FEED_BRANCH}/{FEED_FILE}"
    )

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "feed"
        work.mkdir()
        # Shallow clone of feed branch, or orphan if missing.
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", FEED_BRANCH, remote, str(work)],
            capture_output=True,
            text=True,
        )
        if clone.returncode != 0:
            subprocess.run(
                ["git", "clone", "--depth", "1", remote, str(work)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "checkout", "--orphan", FEED_BRANCH],
                cwd=work,
                check=True,
                capture_output=True,
                text=True,
            )
            # Clear any checked-out files from default branch.
            for path in work.iterdir():
                if path.name == ".git":
                    continue
                if path.is_file():
                    path.unlink()
                else:
                    subprocess.run(["rm", "-rf", str(path)], check=False)

        (work / FEED_FILE).write_text(ics_text, encoding="utf-8")
        readme = (
            "# Markets calendar feed\n\n"
            "Subscribe in Google Calendar (lightbourncal@gmail.com):\n\n"
            f"`{url}`\n\n"
            "Then Notion Calendar (connected to that Gmail) will show Fed / "
            "macro / earnings events automatically.\n"
        )
        (work / "README.md").write_text(readme, encoding="utf-8")

        subprocess.run(["git", "config", "user.email", "bot@users.noreply.github.com"],
                       cwd=work, check=True)
        subprocess.run(["git", "config", "user.name", "markets-calendar-bot"],
                       cwd=work, check=True)
        subprocess.run(["git", "add", FEED_FILE, "README.md"], cwd=work, check=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=work,
            capture_output=True,
            text=True,
            check=True,
        )
        if not status.stdout.strip():
            print("ICS feed unchanged; nothing to push.")
            return url
        subprocess.run(
            ["git", "commit", "-m", "Update markets ICS feed"],
            cwd=work,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", FEED_BRANCH],
            cwd=work,
            check=True,
            capture_output=True,
            text=True,
        )
    print(f"Published ICS feed: {url}")
    return url


def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"
    print(f"Fetching financial announcements (next {DEFAULT_DAYS_AHEAD} days)...")
    events = fetch_financial_announcements(DEFAULT_DAYS_AHEAD)
    print(f"Got {len(events)} events for ICS feed.")
    ics = build_ics(events)
    Path(FEED_FILE).write_text(ics, encoding="utf-8")
    print(f"Wrote local {FEED_FILE} ({len(ics)} bytes).")
    for ev in events:
        print(f"- {ev['start']} {ev['title']}")
    if dry_run:
        print("DRY_RUN=1; not publishing to calendar-feed branch.")
        return
    url = publish_ics(ics)
    print()
    print("Add this URL in Google Calendar → Settings → Add calendar → From URL:")
    print(url)
    print("Notion Calendar will pick it up from lightbourncal@gmail.com.")


if __name__ == "__main__":
    main()
