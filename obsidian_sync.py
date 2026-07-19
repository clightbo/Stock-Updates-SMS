"""Sync markets + interview notes into the Finance Command Center vault.

Writes:
  obsidian-vault/03_Finance_Data/Markets/Calendar.md
  obsidian-vault/03_Finance_Data/Markets/Watchlist.md
  obsidian-vault/01_Projects/Interview Prep/This Week.md

Run via GitHub Actions (see .github/workflows/obsidian-vault-sync.yml) or locally:
  python obsidian_sync.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from financial_calendar import (
    DEFAULT_DAYS_AHEAD,
    fetch_financial_announcements,
    get_watchlist,
)
from interview_ideas import weekly_interview_idea

VAULT = Path(__file__).resolve().parent / "obsidian-vault"
ET_LABEL = "ET"

MARKETS = VAULT / "03_Finance_Data" / "Markets"
INTERVIEW = VAULT / "01_Projects" / "Interview Prep"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def write_calendar() -> int:
    events = fetch_financial_announcements(DEFAULT_DAYS_AHEAD)
    lines = [
        "---",
        "tags:",
        "  - markets",
        "  - calendar",
        "  - fed",
        "---",
        "",
        "# Markets Calendar",
        "",
        f"_Updated {_now_stamp()}. Auto-generated from Stock-Updates-SMS._",
        "",
        "| When (ET) | Impact | Type | Event |",
        "|---|---|---|---|",
    ]
    for ev in events:
        start = ev["start"]
        when = start.replace("T", " ")[:16]
        if len(start) > 16 and (start.endswith("-04:00") or start.endswith("-05:00")):
            when = f"{when} {ET_LABEL}"
        impact = ev.get("impact") or ""
        kind = (ev.get("kind") or "").title()
        title = ev["title"].removeprefix("[Markets] ").strip()
        notes = (ev.get("notes") or "").replace("|", "/")
        lines.append(f"| {when} | {impact} | {kind} | **{title}** — {notes} |")

    lines.extend([
        "",
        "## By type",
        "",
    ])
    by_kind: dict[str, list[dict]] = {}
    for ev in events:
        by_kind.setdefault(ev.get("kind") or "other", []).append(ev)
    for kind, items in sorted(by_kind.items()):
        lines.append(f"### {kind.title()}")
        lines.append("")
        for ev in items:
            when = ev["start"].replace("T", " ")[:16]
            title = ev["title"].removeprefix("[Markets] ").strip()
            lines.append(f"- **{when}** — {title}")
        lines.append("")

    path = MARKETS / "Calendar.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(events)} events).")
    return len(events)


def write_interview() -> None:
    idea = weekly_interview_idea()
    lines = [
        "---",
        "tags:",
        "  - interview",
        "  - pitch",
        "---",
        "",
        "# Interview Idea — This Week",
        "",
        f"_Updated {_now_stamp()}. Same pitch Mon–Sun so you can rehearse it._",
        "",
        "## Pitch",
        "",
        idea,
        "",
        "## Rehearsal checklist",
        "",
        "- [ ] One-sentence thesis",
        "- [ ] Catalyst (what changes the stock)",
        "- [ ] Key risk",
        "- [ ] Tie to something in [[03_Finance_Data/Markets/Calendar]] or today's tape",
        "",
        "## Related",
        "",
        "- [[03_Finance_Data/Markets/Calendar]]",
        "- [[03_Finance_Data/Markets/Watchlist]]",
        "- [[00_Inbox/Latest Briefing]]",
        "",
    ]
    path = INTERVIEW / "This Week.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}.")


def write_watchlist() -> None:
    tickers = get_watchlist()
    lines = [
        "---",
        "tags:",
        "  - markets",
        "  - watchlist",
        "---",
        "",
        "# Watchlist",
        "",
        f"_Updated {_now_stamp()}._",
        "",
        "Tickers from `WATCHLIST` (or defaults):",
        "",
    ]
    for t in tickers:
        lines.append(f"- **{t}**")
    lines.extend([
        "",
        "Add your own research notes under each ticker as needed.",
        "",
        "See also [[03_Finance_Data/Markets/Calendar]] for upcoming earnings on these names.",
        "",
    ])
    path = MARKETS / "Watchlist.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}.")


def main() -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    MARKETS.mkdir(parents=True, exist_ok=True)
    INTERVIEW.mkdir(parents=True, exist_ok=True)
    print(f"Syncing Finance Command Center at {VAULT} ...")
    n = write_calendar()
    write_interview()
    write_watchlist()
    print(f"Done. {n} calendar rows. Open `{VAULT.name}` as an Obsidian vault.")


if __name__ == "__main__":
    main()
