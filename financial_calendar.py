"""Fetch important US macro releases and watchlist earnings for planner/calendar sync."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

ECON_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_WATCHLIST = ["SPY", "QQQ", "DIA", "AAPL", "NVDA", "MSFT"]
# Fallback Eastern offset when a feed timestamp has no tz (DST-aware enough for summer).
ET = timezone(timedelta(hours=-4))
# Near-term macro/earnings window. FOMC is loaded for the rest of the year
# separately (see fetch_fomc_announcements).
DEFAULT_DAYS_AHEAD = 90

# Official remaining 2026 FOMC *decision* days (rate announcement 2:00 PM ET).
# Source: federalreserve.gov / FOMC calendars. SEP = includes dot plot.
FOMC_DECISION_DAYS_2026 = [
    # (date ISO, has_sep)
    ("2026-07-29", False),
    ("2026-09-16", True),
    ("2026-10-28", False),
    ("2026-12-09", True),
]
FOMC_DECISION_DAYS_2027 = [
    ("2027-01-27", False),
    ("2027-03-17", True),
    ("2027-04-28", False),
    ("2027-06-16", True),
    ("2027-07-28", False),
    ("2027-09-15", True),
    ("2027-10-27", False),
    ("2027-12-08", True),
]


def get_watchlist() -> list[str]:
    raw = os.environ.get("WATCHLIST", "")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return tickers or DEFAULT_WATCHLIST


def _parse_econ_datetime(value: str) -> datetime | None:
    """Parse feed timestamps like 2026-07-09T08:30:00-04:00."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_important_econ(title: str, impact: str) -> bool:
    title_l = title.lower()
    if impact in ("High", "Medium"):
        return True
    # Always keep Fed/FOMC/Powell even when impact is Low.
    return any(
        kw in title_l
        for kw in ("fomc", "fed ", "federal reserve", "powell", "warsh", "jefferson")
    )


def _classify_kind(title: str, default: str = "macro") -> str:
    title_l = title.lower()
    if any(kw in title_l for kw in ("fomc", "fed ", "federal reserve", "powell",
                                     "warsh", "jefferson", "monetary policy")):
        return "fed"
    return default


def _iso_with_offset(dt: datetime) -> str:
    """Notion calendar views need a timezone offset on timed dates."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.isoformat(timespec="minutes")


def fetch_econ_announcements(days_ahead: int = DEFAULT_DAYS_AHEAD) -> list[dict]:
    """US macro releases that can move markets (high/medium + Fed)."""
    try:
        events = requests.get(
            ECON_CALENDAR_URL,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        ).json()
    except Exception as exc:
        print(f"warning: econ calendar fetch failed: {exc}", file=sys.stderr)
        return []

    now = datetime.now(ET)
    end = now + timedelta(days=days_ahead)
    rows = []
    for ev in events:
        if ev.get("country") != "USD":
            continue
        title = ev.get("title", "").strip()
        impact = ev.get("impact", "") or "Low"
        if not title or not _is_important_econ(title, impact):
            continue
        start_dt = _parse_econ_datetime(ev.get("date", ""))
        if not start_dt or start_dt < now or start_dt > end:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=ET)
        end_dt = start_dt + timedelta(minutes=30)
        notes = f"{impact} impact"
        if ev.get("forecast"):
            notes += f"; forecast {ev['forecast']}, prev {ev.get('previous', '?')}"
        kind = _classify_kind(title, "macro")
        rows.append({
            "title": f"[Markets] {title}",
            "start": _iso_with_offset(start_dt),
            "end": _iso_with_offset(end_dt),
            "all_day": False,
            "notes": notes,
            "kind": kind,
            "impact": impact if impact in ("High", "Medium", "Low") else "Low",
        })
    return rows


def fetch_fomc_announcements(days_ahead: int = 400) -> list[dict]:
    """FOMC rate-decision days for the rest of this year (+ next), timed 2:00 PM ET.

    Macro RSS only covers ~this week, so FOMC is loaded from a year calendar
    so Notion/Google show Fed events months ahead.
    """
    now = datetime.now(ET)
    end = now + timedelta(days=days_ahead)
    rows = []

    # Prefer static official decision days (accurate 2pm ET announcements).
    year = now.year
    curated = []
    if year <= 2026:
        curated.extend(FOMC_DECISION_DAYS_2026)
    if year <= 2027:
        curated.extend(FOMC_DECISION_DAYS_2027)

    seen_days: set[str] = set()
    for day, has_sep in curated:
        if day in seen_days:
            continue
        seen_days.add(day)
        start_dt = datetime.fromisoformat(f"{day}T14:00:00").replace(tzinfo=ET)
        if start_dt < now or start_dt > end:
            continue
        end_dt = start_dt + timedelta(hours=1)  # covers statement + presser window
        sep = " + SEP/dot plot" if has_sep else ""
        rows.append({
            "title": f"[Markets] FOMC rate decision{sep}",
            "start": _iso_with_offset(start_dt),
            "end": _iso_with_offset(end_dt),
            "all_day": False,
            "notes": (
                "Rate decision typically 2:00 PM ET; Chair press conference "
                "usually ~2:30 PM ET. Source: Federal Reserve FOMC calendar."
            ),
            "kind": "fed",
            "impact": "High",
        })

    rows.sort(key=lambda ev: ev["start"])
    return rows


def fetch_earnings_announcements(days_ahead: int = DEFAULT_DAYS_AHEAD,
                                 tickers: list[str] | None = None) -> list[dict]:
    """Upcoming earnings dates for the watchlist."""
    tickers = tickers or get_watchlist()
    # Skip broad index ETFs — they don't report earnings.
    skip = {"SPY", "QQQ", "DIA", "IWM", "VTI", "VOO"}
    tickers = [t for t in tickers if t not in skip]

    now = datetime.now(ET)
    end = now + timedelta(days=days_ahead)
    rows = []
    for ticker in tickers:
        try:
            cal = yf.Ticker(ticker).calendar or {}
        except Exception as exc:
            print(f"warning: earnings lookup failed for {ticker}: {exc}",
                  file=sys.stderr)
            continue
        earnings_dates = cal.get("Earnings Date")
        if not earnings_dates:
            continue
        if not isinstance(earnings_dates, list):
            earnings_dates = [earnings_dates]
        for raw_date in earnings_dates:
            if hasattr(raw_date, "date"):
                day = raw_date.date() if hasattr(raw_date, "hour") else raw_date
            else:
                day = datetime.fromisoformat(str(raw_date)[:10]).date()
            # Market open reminder time (ET) on earnings day.
            start_dt = datetime.combine(day, datetime.min.time()).replace(
                hour=8, minute=0, tzinfo=ET)
            if start_dt < now.replace(hour=0, minute=0, second=0, microsecond=0):
                continue
            if start_dt > end:
                continue
            end_dt = start_dt + timedelta(hours=1)
            est_eps = cal.get("Earnings Average")
            notes = (f"Earnings report; est EPS {est_eps}"
                     if est_eps else "Earnings report")
            rows.append({
                "title": f"[Markets] {ticker} earnings",
                "start": _iso_with_offset(start_dt),
                "end": _iso_with_offset(end_dt),
                "all_day": False,
                "notes": notes,
                "kind": "earnings",
                "impact": "High",
            })
    return rows


def fetch_financial_announcements(days_ahead: int = DEFAULT_DAYS_AHEAD) -> list[dict]:
    """Merge near-term macro + earnings + year-ahead FOMC, sorted by start."""
    events = (
        fetch_econ_announcements(min(days_ahead, 14))  # free macro feed ≈ this week
        + fetch_earnings_announcements(days_ahead)
        + fetch_fomc_announcements(max(days_ahead, 400))
    )
    # Dedupe by title+start day in case feeds overlap.
    seen: set[tuple[str, str]] = set()
    unique = []
    for ev in events:
        key = (ev["title"], ev["start"][:10])
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    unique.sort(key=lambda ev: ev["start"])
    return unique
