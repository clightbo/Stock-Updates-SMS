"""Networking coach — separate Telegram bot for recruiting outreach drafts.

Uses its own bot token so internship / networking messages never mix with
the market-summary bot. Drafts copy-paste LinkedIn and email messages via
GitHub Models when programs are opening or new roles appear.
"""

from __future__ import annotations

import os
import sys

import requests

LLM_MODEL = os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini"
MESSAGE_CHAR_LIMIT = 3800
BOT_SIGNATURE = os.environ.get("NETWORKING_BOT_NAME") or "Campus Coach"

# Student profile baked in for outreach drafts (edit here if things change).
STUDENT_PROFILE = """
Name context: Texas Tech finance student (use first person as the student).
School: Texas Tech University, Rawls College of Business, BBA Finance, GPA 4.0,
Honors College, expected graduation May 2029 (rising sophomore).
Background: Hispanic. Dallas and NYC interest for finance roles.
Experience: Project Destined (Dallas real estate PE), McDonald Capital Management
externship (systematic equity / AM), SEO EDGE (markets track), Goldman Sachs
Possibilities Series, Raider Capital Group (Head of Internal Ops), Finance
Ambassador, Rawls Banking Association VP Professional Development, DC banking
industry trip (<3% acceptance).
Target roles: global markets / S&T, markets rotations, equity research, AM, IB.
Locations: Dallas metro and New York.
""".strip()

NETWORKING_PROMPT = """You are {bot_name}, a networking coach for a finance student.
Write copy-paste outreach the student can send BEFORE applications open or right
when a program is announced. Be specific, warm, and professional — not cheesy.

Student profile:
{profile}

Program to network for:
Firm: {firm}
Program: {title}
Division: {division}
Location: {location}
Timing: {timing}
URL: {url}
Extra context: {notes}

Reply in plain text only (no markdown). Structure exactly:

WHO TO MESSAGE (2-4 bullet lines, plain dashes)
- ...

LINKEDIN NOTE (under 280 chars, copy-paste ready)
...

LONGER LINKEDIN / EMAIL (under 600 chars, copy-paste ready)
Subject: ...
...
""".strip()


def get_networking_telegram_credentials() -> tuple[str, str]:
    token = (
        os.environ.get("NETWORKING_TELEGRAM_BOT_TOKEN", "").strip().removeprefix("bot")
    )
    chat_id = os.environ.get("NETWORKING_TELEGRAM_CHAT_ID", "").strip()
    # Reuse main chat id if only the networking token is new.
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        return "", ""
    if ":" not in token:
        sys.exit("error: NETWORKING_TELEGRAM_BOT_TOKEN doesn't look like a bot token.")
    if not chat_id.lstrip("-").isdigit():
        sys.exit("error: NETWORKING_TELEGRAM_CHAT_ID doesn't look like a chat id.")
    return token, chat_id


def send_networking_telegram(body: str) -> None:
    token, chat_id = get_networking_telegram_credentials()
    if not token:
        print("note: NETWORKING_TELEGRAM_BOT_TOKEN not set; skipping networking bot.",
              file=sys.stderr)
        return
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": body},
        timeout=30,
    )
    response.raise_for_status()
    print("Networking bot message sent.")


def _fallback_outreach(
    *,
    firm: str,
    title: str,
    division: str = "",
    location: str = "",
    timing: str = "",
) -> str:
    return (
        f"WHO TO MESSAGE\n"
        f"- TTU alumni at {firm} on LinkedIn (Finance / Markets)\n"
        f"- SEO EDGE mentor\n"
        f"- RBA speaker or Rawls professor with {firm} contacts\n"
        f"- Dallas or NYC office analysts if on LinkedIn\n\n"
        f"LINKEDIN NOTE\n"
        f"Hi [Name] — I'm a Finance major at Texas Tech (Class of 2029) "
        f"interested in {title} at {firm}. I'd love 15 minutes of advice "
        f"before applications open. Thank you!\n\n"
        f"LONGER LINKEDIN / EMAIL\n"
        f"Subject: Texas Tech student — {title} at {firm}\n"
        f"Hi [Name], I'm a sophomore Finance major at Texas Tech (4.0, Honors "
        f"College) targeting {division or 'markets/IB'} in {location or 'Dallas/NYC'}. "
        f"I'm preparing for {title} ({timing}) and would appreciate any guidance "
        f"on the process or your experience at {firm}.\n"
        f"Best,\n[Your name]"
    )


def draft_outreach(
    *,
    firm: str,
    title: str,
    division: str = "",
    location: str = "",
    timing: str = "",
    url: str = "",
    notes: str = "",
) -> str:
    if not os.environ.get("GITHUB_TOKEN"):
        return _fallback_outreach(
            firm=firm, title=title, division=division,
            location=location, timing=timing,
        )

    system = NETWORKING_PROMPT.format(
        bot_name=BOT_SIGNATURE,
        profile=STUDENT_PROFILE,
        firm=firm,
        title=title,
        division=division or "Finance",
        location=location or "Dallas / NYC",
        timing=timing or "opening soon",
        url=url or "(see firm careers page)",
        notes=notes or "",
    )
    try:
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
                    {"role": "system", "content": system},
                    {"role": "user", "content": "Write the outreach drafts now."},
                ],
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"warning: LLM outreach draft failed ({exc}); using template.",
              file=sys.stderr)
        return _fallback_outreach(
            firm=firm, title=title, division=division,
            location=location, timing=timing,
        )


def format_coach_message(
    *,
    headline: str,
    programs: list[dict],
    drafts: list[str],
) -> str:
    lines = [f"{BOT_SIGNATURE.upper()} — {headline}", ""]
    for prog, draft in zip(programs, drafts):
        lines.append(f"▸ {prog['firm']} — {prog['title']}")
        if prog.get("timing"):
            lines.append(f"  {prog['timing']}")
        lines.append("")
        lines.append(draft.strip())
        lines.append("")
        if prog.get("url"):
            lines.append(prog["url"])
        lines.append("—" * 20)
        lines.append("")
    lines.append(f"— {BOT_SIGNATURE}")
    body = "\n".join(lines).strip()
    if len(body) > MESSAGE_CHAR_LIMIT:
        body = body[: MESSAGE_CHAR_LIMIT - 3] + "..."
    return body


def build_networking_messages(
    early_alerts: list,
    new_roles: list,
    *,
    max_programs: int = 2,
) -> list[str]:
    """Return one or more Telegram messages with outreach drafts."""
    messages: list[str] = []
    batch: list[dict] = []
    drafts: list[str] = []

    def flush(headline: str) -> None:
        nonlocal batch, drafts
        if batch and drafts:
            messages.append(format_coach_message(
                headline=headline, programs=batch, drafts=drafts,
            ))
        batch, drafts = [], []

    for posting in early_alerts[:max_programs]:
        timing = posting.alert_window or "opening in 1–2 months"
        if posting.notes and "opens ~" in posting.notes:
            timing = posting.notes.split(".")[0]
        batch.append({
            "firm": posting.firm,
            "title": posting.title,
            "timing": timing,
            "url": posting.url,
        })
        try:
            drafts.append(draft_outreach(
                firm=posting.firm,
                title=posting.title,
                division=posting.division,
                location=posting.location,
                timing=timing,
                url=posting.url,
                notes=posting.notes,
            ))
        except Exception as exc:
            print(f"warning: outreach draft failed for {posting.firm} ({exc})",
                  file=sys.stderr)
            drafts.append(_fallback_outreach(
                firm=posting.firm, title=posting.title,
                division=posting.division, location=posting.location,
                timing=timing,
            ))
    if batch:
        flush("start networking before apps open")

    batch, drafts = [], []
    for posting in new_roles[:max_programs]:
        batch.append({
            "firm": posting.firm,
            "title": posting.title,
            "timing": "applications open / just posted",
            "url": posting.url,
        })
        try:
            drafts.append(draft_outreach(
                firm=posting.firm,
                title=posting.title,
                division=posting.division,
                location=posting.location,
                timing="apply now — also message alumni same day",
                url=posting.url,
                notes=posting.notes,
            ))
        except Exception as exc:
            print(f"warning: outreach draft failed for {posting.firm} ({exc})",
                  file=sys.stderr)
            drafts.append(_fallback_outreach(
                firm=posting.firm, title=posting.title,
                division=posting.division, location=posting.location,
                timing="applications open",
            ))
    if batch:
        flush("new role — apply + network today")

    return messages
