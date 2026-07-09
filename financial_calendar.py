"""Fetch important US macro releases and watchlist earnings for planner/calendar sync."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

ECON_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_WATCHLIST = ["SPY", "QQQ", "DIA", "AAPL", "NVDA", "MSFT"]
# US Eastern offset baked into the econ feed timestamps (e.g. -04:00 in summer).
ET = timezone(timedelta(hours=-4))


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
    return any(kw in title_l for kw in ("fomc", "fed ", "federal reserve", "powell"))


def fetch_econ_announcements(days_ahead: int = 7) -> list[dict]:
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
        impact = ev.get("impact", "")
        if not title or not _is_important_econ(title, impact):
            continue
        start_dt = _parse_econ_datetime(ev.get("date", ""))
        if not start_dt or start_dt < now or start_dt > end:
            continue
        notes = f"{impact} impact"
        if ev.get("forecast"):
            notes += f"; forecast {ev['forecast']}, prev {ev.get('previous', '?')}"
        rows.append({
            "title": f"[Markets] {title}",
            "start": start_dt.strftime("%Y-%m-%dT%H:%M"),
            "end": (start_dt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"),
            "all_day": False,
            "notes": notes,
            "kind": "econ",
        })
    return rows


def fetch_earnings_announcements(days_ahead: int = 7,
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
            start_dt = datetime.combine(day, datetime.min.time()).replace(tzinfo=ET)
            if start_dt < now.replace(hour=0, minute=0, second=0, microsecond=0):
                continue
            if start_dt > end:
                continue
            est_eps = cal.get("Earnings Average")
            notes = f"Earnings report; est EPS {est_eps}" if est_eps else "Earnings report"
            rows.append({
                "title": f"[Markets] {ticker} earnings",
                "start": start_dt.strftime("%Y-%m-%dT08:00"),
                "end": start_dt.strftime("%Y-%m-%dT09:00"),
                "all_day": False,
                "notes": notes,
                "kind": "earnings",
            })
    return rows


def fetch_financial_announcements(days_ahead: int = 7) -> list[dict]:
    """Merge macro + earnings, sorted by start time."""
    events = fetch_econ_announcements(days_ahead) + fetch_earnings_announcements(days_ahead)
    events.sort(key=lambda ev: ev["start"])
    return events
