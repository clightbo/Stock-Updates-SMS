"""Job sources and filters for the finance internship tracker.

Polls public Greenhouse and Workday career APIs plus a curated list of
sophomore / discovery program pages that typically open in early fall.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

USER_AGENT = "internship-tracker/1.0 (+github-actions)"

# ------------------------------------------------------------------ filters

DALLAS_KEYWORDS = ("dallas", "dfw", "irving", "plano", "richardson", "texas")
DIVISION_KEYWORDS = {
    "AM": (
        "asset management", "wealth management", "private wealth", "pwm",
        "portfolio management", "investment management", "pwm", "pwm ",
        "private bank", "wealth advisor",
    ),
    "S&T": (
        "sales and trading", "global markets", "securities", "fixed income",
        "equities", "commodities", "macro", "rates", "fx ", "foreign exchange",
        "markets intern", "trading intern",
    ),
    "IB": (
        "investment banking", "ibd", "corporate finance", "m&a", "mergers",
        "advisory intern", "capital markets intern",
    ),
}
SOPHOMORE_KEYWORDS = (
    "sophomore", "discovery", "underclassman", "underclassmen", "freshman",
    "first year", "first-year", "early insight", "early insights", "insight day",
    "explore", "possibilities", "launch", "winning women", "diversity symposium",
    "emerging talent", "early identification", "edge program", "future leaders",
    "campus connect", "introductory", "freshman internship", "soph intern",
    "rising sophomore", "class of 2028", "class of 2029",
)
INTERNSHIP_KEYWORDS = (
    "intern", "internship", "summer analyst", "off-cycle", "co-op", "coop",
    "campus", "student program", "analyst program",
)
NOISE_KEYWORDS = (
    "ediscovery", "e-discovery", "vice president", "director", "manager",
    "full time", "full-time", "registered service associate",
    "financial services representative", "recruiting systems analyst",
)

DEFAULT_LOCATIONS = ("dallas", "dfw", "texas")
DEFAULT_DIVISIONS = ("AM", "S&T", "IB")
DEFAULT_CLASS_YEARS = ("Sophomore", "Discovery")


@dataclass
class JobPosting:
    title: str
    firm: str
    url: str
    location: str = ""
    division: str = ""
    class_year: str = ""
    program_type: str = ""
    source: str = ""
    posted: str = ""
    notes: str = ""
    raw_text: str = field(default="", repr=False)

    def dedupe_key(self) -> str:
        base = "|".join((self.firm, self.title, self.location, self.url))
        return hashlib.sha1(base.encode()).hexdigest()[:16]


def _text_blob(posting: JobPosting) -> str:
    return " ".join(
        p for p in (
            posting.title, posting.location, posting.notes,
            posting.raw_text, posting.program_type,
        ) if p
    ).lower()


def classify_division(text: str) -> str:
    for division, keywords in DIVISION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return division
    return ""


def classify_class_year(posting: JobPosting) -> str:
    text = _title_location_blob(posting)
    if any(kw in text for kw in SOPHOMORE_KEYWORDS):
        return "Sophomore"
    if "junior" in text or "2027 summer" in text or "2026 summer" in text:
        return "Junior"
    return ""


def classify_program_type(posting: JobPosting) -> str:
    text = _title_location_blob(posting)
    if any(kw in text for kw in SOPHOMORE_KEYWORDS):
        return "Discovery"
    if "summer analyst" in text or "summer intern" in text:
        return "Summer Analyst"
    if "off-cycle" in text or "off cycle" in text:
        return "Off-Cycle"
    if "intern" in text:
        return "Internship"
    return "Program"


def _title_location_blob(posting: JobPosting) -> str:
    return f"{posting.title} {posting.location}".lower()


def location_matches(posting: JobPosting, locations: tuple[str, ...]) -> bool:
    blob = _title_location_blob(posting)
    if any(loc in blob for loc in locations):
        return True
    # National discovery programs (source = program watch or email) can omit city.
    if posting.source in ("Program watch", "Email"):
        return True
    # Multi-city campus roles sometimes list "Multiple Locations".
    if "multiple location" in blob or "various location" in blob:
        return True
    return False


def title_has_intern_signal(posting: JobPosting) -> bool:
    title = posting.title.lower()
    return any(kw in title for kw in INTERNSHIP_KEYWORDS + SOPHOMORE_KEYWORDS)


def title_has_soph_signal(posting: JobPosting) -> bool:
    return any(kw in posting.title.lower() for kw in SOPHOMORE_KEYWORDS)


def is_noise(text: str) -> bool:
    blob = text.lower()
    if any(kw in blob for kw in NOISE_KEYWORDS):
        # Allow campus postings that happen to mention VP in metadata.
        if any(kw in blob for kw in INTERNSHIP_KEYWORDS + SOPHOMORE_KEYWORDS):
            return False
        return True
    return False


def is_relevant(
    posting: JobPosting,
    *,
    locations: tuple[str, ...] = DEFAULT_LOCATIONS,
    divisions: tuple[str, ...] = DEFAULT_DIVISIONS,
    class_years: tuple[str, ...] = DEFAULT_CLASS_YEARS,
    discovery_season: bool = False,
) -> bool:
    blob = _text_blob(posting)
    if is_noise(blob):
        return False

    # Job-board postings must look like campus roles in the title.
    if posting.source in ("Greenhouse", "Workday") and not title_has_intern_signal(posting):
        return False

    if not location_matches(posting, locations):
        return False

    division = posting.division or classify_division(blob)
    class_year = posting.class_year or classify_class_year(posting)
    soph_in_title = title_has_soph_signal(posting)

    if divisions and division and division not in divisions:
        if not soph_in_title and posting.source != "Program watch":
            return False

    if class_years:
        if class_year and class_year not in class_years:
            return False
        if not class_year and posting.source in ("Greenhouse", "Workday"):
            # Outside explicit discovery titles, only keep Dallas intern roles
            # during discovery season.
            if not (discovery_season and "intern" in posting.title.lower()):
                return False
            if not any(loc in _title_location_blob(posting) for loc in locations):
                return False

    posting.division = division or posting.division
    posting.class_year = class_year or posting.class_year
    posting.program_type = posting.program_type or classify_program_type(posting)
    return True


# ------------------------------------------------------------- API sources

GREENHOUSE_BOARDS = (
    {"firm": "Jane Street", "board": "janestreet"},
    {"firm": "Point72", "board": "point72"},
    {"firm": "IMC Trading", "board": "imc"},
    {"firm": "Jump Trading", "board": "jumptrading"},
    {"firm": "StepStone", "board": "stepstone"},
    {"firm": "Schonfeld", "board": "schonfeld"},
    {"firm": "Flow Traders", "board": "flowtraders"},
    {"firm": "Virtu Financial", "board": "virtu"},
    {"firm": "William Blair", "board": "williamblair"},
    {"firm": "AQR", "board": "aqr"},
    {"firm": "ExodusPoint", "board": "exoduspoint"},
)

WORKDAY_SOURCES = (
    {
        "firm": "Morgan Stanley",
        "tenant": "ms",
        "site": "External",
        "wd_host": "ms.wd5.myworkdayjobs.com",
        "job_board_url": "https://ms.wd5.myworkdayjobs.com/en-US/External",
        "searches": (
            "2027 Summer Analyst",
            "2026 Summer Analyst",
            "Early Insights",
            "Sophomore",
            "Discovery Program",
            "Global Markets Intern",
            "Investment Banking Intern",
            "Asset Management Intern",
            "Dallas Intern",
        ),
    },
)

# Curated sophomore / discovery programs — typically open late Aug through Sep.
# The tracker watches each page for apply signals and content changes.
CURATED_PROGRAMS = (
    {
        "firm": "Goldman Sachs",
        "title": "Sophomore Externship / Discovery",
        "division": "IB",
        "url": "https://www.goldmansachs.com/careers/students/programs/americas",
        "typical_open": "August-September",
    },
    {
        "firm": "JPMorgan",
        "title": "Winning Women / Launching Leaders",
        "division": "IB",
        "url": "https://careers.jpmorgan.com/us/en/students/programs",
        "typical_open": "August-September",
    },
    {
        "firm": "Bank of America",
        "title": "Campus Discovery Programs",
        "division": "IB",
        "url": "https://careers.bankofamerica.com/en-us/students/campus-programs",
        "typical_open": "August-September",
    },
    {
        "firm": "Citi",
        "title": "Early Insight Programs",
        "division": "IB",
        "url": "https://jobs.citi.com/campus",
        "typical_open": "August-September",
    },
    {
        "firm": "Morgan Stanley",
        "title": "Early Insights / Discovery",
        "division": "IB",
        "url": "https://www.morganstanley.com/careers/career-opportunities-students-graduates",
        "typical_open": "August-September",
    },
    {
        "firm": "Wells Fargo",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.wellsfargojobs.com/en/jobs/?area=Campus",
        "typical_open": "August-September",
    },
    {
        "firm": "Evercore",
        "title": "Sophomore Programs",
        "division": "IB",
        "url": "https://www.evercore.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "Moelis",
        "title": "Underclassman Programs",
        "division": "IB",
        "url": "https://www.moelis.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "Houlihan Lokey",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://hl.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "Lazard",
        "title": "Diversity / Discovery Programs",
        "division": "IB",
        "url": "https://www.lazard.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "PJT Partners",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.pjtpartners.com/careers/students",
        "typical_open": "August-September",
    },
    {
        "firm": "Barclays",
        "title": "Discovery / Springboard",
        "division": "S&T",
        "url": "https://search.jobs.barclays/search-jobs",
        "typical_open": "August-September",
    },
    {
        "firm": "UBS",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.ubs.com/global/en/careers/students-and-graduates.html",
        "typical_open": "August-September",
    },
    {
        "firm": "BlackRock",
        "title": "Sophomore / Discovery",
        "division": "AM",
        "url": "https://careers.blackrock.com/students-and-graduates",
        "typical_open": "August-September",
    },
    {
        "firm": "PIMCO",
        "title": "Career Discovery",
        "division": "AM",
        "url": "https://careers.pimco.com/careers/students-graduates",
        "typical_open": "August-September",
    },
)

APPLY_OPEN_SIGNALS = (
    "apply now", "applications are open", "application is open",
    "apply today", "submit your application", "start application",
    "applications open", "now accepting applications", "register now",
    "sign up now", "registration is open",
)
APPLY_CLOSED_SIGNALS = (
    "applications are closed", "application closed", "registration closed",
    "no longer accepting", "deadline has passed",
)

EMAIL_RECRUITING_FROM = (
    "greenhouse", "workday", "icims", "lever.co", "handshake",
    "morganstanley", "goldmansachs", "jpmorgan", "jpmchase", "citi.com",
    "bankofamerica", "wellsfargo", "evercore", "moelis", "lazard",
    "pjtpartners", "barclays", "ubs.com", "blackrock", "pimco",
    "campus", "recruiting", "talent", "university", "college",
)
EMAIL_RECRUITING_SUBJECT = (
    "internship", "intern ", "summer analyst", "discovery", "sophomore",
    "early insight", "campus", "application", "recruiting", "career",
    "global markets", "investment banking", "asset management",
    "dallas", "dfw",
)


def discovery_season_active(month: int | None = None) -> bool:
    """Early-fall recruiting window when sophomore programs open."""
    month = month or datetime.now().month
    return month in (8, 9, 10, 11)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_greenhouse_jobs(session: requests.Session | None = None) -> list[JobPosting]:
    session = session or _session()
    postings: list[JobPosting] = []
    for src in GREENHOUSE_BOARDS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{src['board']}/jobs"
        try:
            response = session.get(
                url, params={"content": "true"}, timeout=30,
            )
            if response.status_code != 200:
                print(f"warning: Greenhouse {src['board']} returned "
                      f"{response.status_code}", flush=True)
                continue
            for job in response.json().get("jobs", []):
                location = (job.get("location") or {}).get("name", "")
                content = re.sub(r"<[^>]+>", " ", job.get("content") or "")
                postings.append(JobPosting(
                    title=job.get("title", ""),
                    firm=src["firm"],
                    url=job.get("absolute_url", ""),
                    location=location,
                    source="Greenhouse",
                    posted=(job.get("updated_at") or "")[:10],
                    raw_text=content,
                ))
        except requests.RequestException as exc:
            print(f"warning: Greenhouse {src['board']} failed ({exc})",
                  flush=True)
    return postings


def fetch_workday_jobs(session: requests.Session | None = None) -> list[JobPosting]:
    session = session or _session()
    postings: list[JobPosting] = []
    seen: set[str] = set()

    for src in WORKDAY_SOURCES:
        api = (
            f"https://{src['wd_host']}/wday/cxs/"
            f"{src['tenant']}/{src['site']}/jobs"
        )
        base = src["job_board_url"].rstrip("/")
        for term in src["searches"]:
            try:
                response = session.post(
                    api,
                    json={"limit": 50, "offset": 0, "searchText": term},
                    timeout=30,
                )
                if response.status_code != 200:
                    continue
                data = response.json()
                for job in data.get("jobPostings", []):
                    path = job.get("externalPath", "")
                    url = f"{base}{path}" if path else base
                    key = f"{src['firm']}|{job.get('title','')}|{url}"
                    if key in seen:
                        continue
                    seen.add(key)
                    postings.append(JobPosting(
                        title=job.get("title", ""),
                        firm=src["firm"],
                        url=url,
                        location=job.get("locationsText", ""),
                        source="Workday",
                        posted=job.get("postedOn", ""),
                        notes=f"Search: {term}",
                        raw_text=term,
                    ))
            except requests.RequestException as exc:
                print(f"warning: Workday {src['firm']} search '{term}' "
                      f"failed ({exc})", flush=True)
    return postings


def _page_apply_status(text: str) -> str:
    blob = text.lower()
    if any(sig in blob for sig in APPLY_CLOSED_SIGNALS):
        return "Closed"
    if any(sig in blob for sig in APPLY_OPEN_SIGNALS):
        return "Open"
    return "Watch"


def fetch_curated_programs(session: requests.Session | None = None) -> list[JobPosting]:
    session = session or _session()
    postings: list[JobPosting] = []
    for prog in CURATED_PROGRAMS:
        try:
            response = session.get(prog["url"], timeout=30)
            if response.status_code >= 400:
                print(f"warning: program page {prog['firm']} returned "
                      f"{response.status_code}", flush=True)
                continue
            text = re.sub(r"<[^>]+>", " ", response.text)
            text = re.sub(r"\s+", " ", text)
            status = _page_apply_status(text)
            dallas_hit = any(k in text.lower() for k in DALLAS_KEYWORDS)
            soph_hit = any(k in text.lower() for k in SOPHOMORE_KEYWORDS)
            intern_hit = any(k in text.lower() for k in INTERNSHIP_KEYWORDS)
            notes = (
                f"Page status: {status}. Typical open: {prog['typical_open']}."
                + (" Mentions Dallas." if dallas_hit else "")
            )
            if status == "Open" or (discovery_season_active() and (soph_hit or intern_hit)):
                postings.append(JobPosting(
                    title=prog["title"],
                    firm=prog["firm"],
                    url=prog["url"],
                    location="Dallas" if dallas_hit else "National",
                    division=prog.get("division", ""),
                    class_year="Sophomore" if soph_hit else "",
                    program_type="Discovery",
                    source="Program watch",
                    notes=notes,
                    raw_text=text[:4000],
                ))
        except requests.RequestException as exc:
            print(f"warning: program watch {prog['firm']} failed ({exc})",
                  flush=True)
    return postings


def postings_from_recruiting_emails(emails: list[dict]) -> list[JobPosting]:
    postings: list[JobPosting] = []
    for em in emails:
        from_addr = (em.get("from") or "").lower()
        subject = (em.get("subject") or "").lower()
        preview = (em.get("preview") or "").lower()
        blob = f"{from_addr} {subject} {preview}"

        from_hit = any(tok in from_addr for tok in EMAIL_RECRUITING_FROM)
        subject_hit = any(tok in subject for tok in EMAIL_RECRUITING_SUBJECT)
        if not from_hit and not subject_hit:
            continue
        if not any(tok in blob for tok in INTERNSHIP_KEYWORDS + SOPHOMORE_KEYWORDS):
            continue

        url_match = re.search(r"https?://[^\s<>\"']+", em.get("preview") or "")
        url = url_match.group(0) if url_match else ""
        firm = em.get("from", "Unknown").split()[0]
        postings.append(JobPosting(
            title=em.get("subject", "Recruiting email"),
            firm=firm,
            url=url,
            location="",
            source="Email",
            notes=em.get("preview", "")[:300],
            raw_text=blob,
        ))
    return postings


def fetch_all_postings(
    emails: list[dict] | None = None,
    *,
    include_program_watch: bool = True,
) -> list[JobPosting]:
    session = _session()
    postings: list[JobPosting] = []
    postings.extend(fetch_greenhouse_jobs(session))
    postings.extend(fetch_workday_jobs(session))
    if include_program_watch:
        postings.extend(fetch_curated_programs(session))
    if emails:
        postings.extend(postings_from_recruiting_emails(emails))
    return postings


def filter_postings(
    postings: list[JobPosting],
    *,
    locations: tuple[str, ...] | None = None,
    divisions: tuple[str, ...] | None = None,
    class_years: tuple[str, ...] | None = None,
) -> list[JobPosting]:
    locations = locations or DEFAULT_LOCATIONS
    divisions = divisions or DEFAULT_DIVISIONS
    class_years = class_years or DEFAULT_CLASS_YEARS
    season = discovery_season_active()
    matched: list[JobPosting] = []
    for posting in postings:
        copy = JobPosting(**{k: getattr(posting, k) for k in posting.__dataclass_fields__})
        if is_relevant(
            copy,
            locations=locations,
            divisions=divisions,
            class_years=class_years,
            discovery_season=season,
        ):
            matched.append(copy)
    return matched
