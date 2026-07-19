"""Job sources and filters for the finance internship tracker.

Tuned for a Texas Tech Finance (Class of 2029) profile: Dallas / Texas / NYC,
bulge-bracket + elite boutiques across S&T / markets rotations, equity research,
AM, and IB. Prioritizes Hispanic / Latino / Black diversity fellowships.

Polls public Greenhouse and Workday career APIs plus a curated list of
sophomore / discovery program pages that typically open in early fall.
Sends early heads-up alerts 1–2 months before expected open dates.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import requests

USER_AGENT = "internship-tracker/1.0 (+github-actions)"

# ------------------------------------------------------------------ filters

DALLAS_KEYWORDS = (
    "dallas", "dfw", "irving", "plano", "richardson", "fort worth",
    "lubbock", "texas", "tx ",
)
NYC_KEYWORDS = (
    "new york", "nyc", "manhattan", "brooklyn", "jersey city",
)
LOCATION_KEYWORDS = DALLAS_KEYWORDS + NYC_KEYWORDS
DIVISION_KEYWORDS = {
    "AM": (
        "asset management", "wealth management", "private wealth", "pwm",
        "portfolio management", "investment management", "private bank",
        "wealth advisor", "equity research", "research intern", "research analyst",
        "buy-side", "buyside", "portfolio analyst", "fund analyst",
    ),
    "S&T": (
        "sales and trading", "global markets", "securities", "fixed income",
        "equities", "commodities", "macro", "rates", "fx ", "foreign exchange",
        "markets intern", "trading intern", "capital markets", "structured",
        "ficc", "equity derivatives", "markets fellowship", "markets rotation",
        "finance rotation", "rotation program",
    ),
    "IB": (
        "investment banking", "ibd", "corporate finance", "m&a", "mergers",
        "advisory intern", "capital markets intern", "restructuring",
        "leveraged finance", "coverage", "finance rotation",
    ),
}
SOPHOMORE_KEYWORDS = (
    "sophomore", "discovery", "underclassman", "underclassmen", "freshman",
    "first year", "first-year", "early insight", "early insights", "insight day",
    "explore", "possibilities", "launch", "diversity symposium",
    "emerging talent", "early identification", "edge program", "future leaders",
    "campus connect", "introductory", "freshman internship", "soph intern",
    "rising sophomore", "class of 2028", "class of 2029", "seo edge",
    "springboard", "insight series", "career discovery",
)
DIVERSITY_KEYWORDS = (
    "hispanic", "latino", "latina", "latinx", "black", "african american",
    "diversity", "fellowship", "pathways", "dbachieve", "seo", "launching leaders",
    "advancing", "underrepresented", "markets fellowship", "possibilities",
    "insight program", "student leaders",
)
EXCLUDE_KEYWORDS = (
    "winning women", "women in finance", "women in", "women's", "for women",
    "female only", "womens ", "girls who invest", "girl who", "her campus",
    "she can", "women on wall",
)
INTERNSHIP_KEYWORDS = (
    "intern", "internship", "summer analyst", "off-cycle", "co-op", "coop",
    "campus", "student program", "analyst program", "rotation", "finance rotation",
    "externship", "extern", "academy", "fellowship", "insight program",
)
NOISE_KEYWORDS = (
    "ediscovery", "e-discovery", "vice president", "director", "manager",
    "full time", "full-time", "registered service associate",
    "financial services representative", "recruiting systems analyst",
)

DEFAULT_LOCATIONS = (
    "dallas", "dfw", "texas", "lubbock", "irving", "plano", "fort worth",
    "new york", "nyc", "manhattan",
)
DEFAULT_DIVISIONS = ("S&T", "AM", "IB")
DEFAULT_CLASS_YEARS = ("Freshman", "Sophomore", "Discovery")

# How many days before typical open date to send early heads-up alerts.
EARLY_ALERT_WINDOWS = (
    (45, 62, "2 months"),   # ~2 months out — start networking
    (25, 38, "1 month"),    # ~1 month out — prep applications
)


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
    alert_window: str = ""  # set for early heads-up alerts

    def dedupe_key(self) -> str:
        base = "|".join((self.firm, self.title, self.location, self.url))
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def alert_dedupe_key(self, year: int, window: str) -> str:
        base = f"alert|{year}|{window}|{self.firm}|{self.title}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]


@dataclass
class UpcomingProgram:
    firm: str
    title: str
    division: str
    url: str
    opens_month: int
    opens_day: int = 15
    diversity: str = ""
    networking_tip: str = ""
    locations: str = "Dallas / NYC / National"


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
    if any(kw in text for kw in ("freshman", "first year", "first-year", "class of 2029")):
        return "Freshman"
    if any(kw in text for kw in SOPHOMORE_KEYWORDS):
        return "Sophomore"
    if "junior" in text or "2027 summer" in text or "2026 summer" in text:
        return "Junior"
    if "2028 summer" in text:
        return "Sophomore"
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


def is_excluded(posting: JobPosting) -> bool:
    blob = _text_blob(posting)
    return any(kw in blob for kw in EXCLUDE_KEYWORDS)


def is_diversity_program(posting: JobPosting) -> bool:
    blob = _text_blob(posting)
    return any(kw in blob for kw in DIVERSITY_KEYWORDS)


def location_matches(posting: JobPosting, locations: tuple[str, ...]) -> bool:
    blob = _title_location_blob(posting)
    if any(loc in blob for loc in locations):
        return True
    # National discovery / diversity programs can omit city in the title.
    if posting.source in ("Program watch", "Email", "Early alert"):
        return True
    if is_diversity_program(posting):
        return True
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
    if is_noise(blob) or is_excluded(posting):
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
    # Bulge bracket / large AM (where public API exists)
    {"firm": "AQR", "board": "aqr"},
    # Elite boutiques / advisory
    {"firm": "William Blair", "board": "williamblair"},
    {"firm": "Lincoln International", "board": "lincolninternational"},
    # Markets / quant / prop
    {"firm": "Jane Street", "board": "janestreet"},
    {"firm": "Point72", "board": "point72"},
    {"firm": "IMC Trading", "board": "imc"},
    {"firm": "Jump Trading", "board": "jumptrading"},
    {"firm": "Schonfeld", "board": "schonfeld"},
    {"firm": "Flow Traders", "board": "flowtraders"},
    {"firm": "Virtu Financial", "board": "virtu"},
    {"firm": "ExodusPoint", "board": "exoduspoint"},
    # PE / alt (rotation-adjacent)
    {"firm": "StepStone", "board": "stepstone"},
)

_CAMPUS_SEARCHES = (
    "2028 Summer Analyst",
    "2027 Summer Analyst",
    "Global Markets Intern",
    "Markets Fellowship",
    "Markets Rotation",
    "Finance Rotation",
    "Sales and Trading Intern",
    "Equity Research Intern",
    "Asset Management Intern",
    "Investment Banking Intern",
    "Sophomore Discovery",
    "Early Insight",
    "Early Insights",
    "Hispanic Fellowship",
    "Latino Fellowship",
    "Black Fellowship",
    "Possibilities",
    "Dallas Intern",
    "New York Intern",
    "Texas Intern",
    "Campus Analyst",
)

WORKDAY_SOURCES = (
    {
        "firm": "Morgan Stanley",
        "tenant": "ms",
        "site": "External",
        "wd_host": "ms.wd5.myworkdayjobs.com",
        "job_board_url": "https://ms.wd5.myworkdayjobs.com/en-US/External",
        "searches": _CAMPUS_SEARCHES,
    },
    {
        "firm": "Citi",
        "tenant": "citi",
        "site": "2",
        "wd_host": "citi.wd5.myworkdayjobs.com",
        "job_board_url": "https://citi.wd5.myworkdayjobs.com/en-US/2",
        "searches": _CAMPUS_SEARCHES,
    },
)

# Programs with known typical open months — used for 1–2 month early Telegram alerts.
# opens_month: month applications usually open (1=Jan … 12=Dec).
UPCOMING_PROGRAMS: tuple[UpcomingProgram, ...] = (
    UpcomingProgram(
        firm="JPMorgan",
        title="Hispanic & Latino Markets Fellowship",
        division="S&T",
        url="https://careers.jpmorgan.com/us/en/students/programs",
        opens_month=8,
        diversity="Hispanic / Latino",
        networking_tip=(
            "Reach out to JPM Dallas & NYC markets analysts on LinkedIn; "
            "ask SEO mentors and TTU alumni who did Launching Leaders."
        ),
    ),
    UpcomingProgram(
        firm="JPMorgan",
        title="Advancing Black Pathways / Black Markets Fellowship",
        division="S&T",
        url="https://careers.jpmorgan.com/us/en/students/programs",
        opens_month=8,
        diversity="Black",
        networking_tip=(
            "Connect with JPM campus recruiters and former fellows before apps open."
        ),
    ),
    UpcomingProgram(
        firm="JPMorgan",
        title="Launching Leaders (Hispanic / Latino)",
        division="IB",
        url="https://careers.jpmorgan.com/us/en/students/programs",
        opens_month=8,
        diversity="Hispanic / Latino",
        networking_tip=(
            "Message TTU alumni at JPM; mention SEO EDGE and your DC banking trip."
        ),
    ),
    UpcomingProgram(
        firm="JPMorgan",
        title="Global Markets / Finance Rotation",
        division="S&T",
        url="https://careers.jpmorgan.com/us/en/students/programs/global-markets",
        opens_month=8,
        networking_tip=(
            "Ask SEO mentors about markets rotation timeline; "
            "reach out to JPM Dallas & NYC desk analysts."
        ),
    ),
    UpcomingProgram(
        firm="JPMorgan",
        title="Investment Banking Sophomore Discovery",
        division="IB",
        url="https://careers.jpmorgan.com/us/en/students/programs",
        opens_month=8,
        networking_tip="Ask RBA alumni and Rawls professors for IB coffee chats.",
    ),
    UpcomingProgram(
        firm="Goldman Sachs",
        title="Possibilities Summit / Sophomore Externship",
        division="IB",
        url="https://www.goldmansachs.com/careers/students/programs",
        opens_month=8,
        diversity="Diversity",
        networking_tip=(
            "You did Possibilities Series — reach out to GS campus contacts "
            "and ask about sophomore externship timeline."
        ),
    ),
    UpcomingProgram(
        firm="Morgan Stanley",
        title="Early Insights (IB / Markets)",
        division="IB",
        url="https://www.morganstanley.com/people-opportunities/students-graduates",
        opens_month=8,
        diversity="Diversity",
        networking_tip="Connect with MS Dallas office on LinkedIn before August.",
    ),
    UpcomingProgram(
        firm="Bank of America",
        title="Student Leaders / Campus Discovery",
        division="IB",
        url="https://campus.bankofamerica.com/",
        opens_month=8,
        diversity="Diversity",
        networking_tip="Ask RBA speakers who work at BofA about the program.",
    ),
    UpcomingProgram(
        firm="Citi",
        title="Early Insight (IB / Markets)",
        division="IB",
        url="https://jobs.citi.com/",
        opens_month=9,
        diversity="Diversity",
        networking_tip="Citi recruits heavily in Dallas and NYC — start outreach in July.",
    ),
    UpcomingProgram(
        firm="Deutsche Bank",
        title="dbAchieve / Sophomore Discovery",
        division="IB",
        url="https://careers.db.com/students-graduates",
        opens_month=8,
        diversity="Diversity",
        networking_tip="dbAchieve is a strong diversity pipeline — ask SEO peers who applied.",
    ),
    UpcomingProgram(
        firm="Barclays",
        title="Discovery / Springboard (Markets)",
        division="S&T",
        url="https://search.jobs.barclays/",
        opens_month=8,
        diversity="Diversity",
        networking_tip="Barclays runs NYC and Dallas events — ask about both offices.",
    ),
    UpcomingProgram(
        firm="Evercore",
        title="Sophomore / Underclassman Discovery",
        division="IB",
        url="https://www.evercore.com/careers/students/",
        opens_month=8,
        networking_tip="Boutiques open early — start networking with Evercore analysts in June.",
    ),
    UpcomingProgram(
        firm="Moelis",
        title="Underclassman Campus Program",
        division="IB",
        url="https://www.moelis.com/",
        opens_month=8,
        networking_tip="Moelis Dallas office — reach out before apps drop.",
    ),
    UpcomingProgram(
        firm="Houlihan Lokey",
        title="Campus Discovery",
        division="IB",
        url="https://hl.com/careers/students/",
        opens_month=8,
        networking_tip="HL has strong Dallas presence — ask TTU alumni there.",
    ),
    UpcomingProgram(
        firm="Lazard",
        title="Diversity Discovery",
        division="IB",
        url="https://www.lazard.com/careers/students/",
        opens_month=8,
        diversity="Diversity",
        networking_tip="Lazard NYC-heavy but recruits nationally — start NYC outreach.",
    ),
    UpcomingProgram(
        firm="PJT Partners",
        title="Campus Discovery",
        division="IB",
        url="https://www.pjtpartners.com/careers/students",
        opens_month=8,
        networking_tip="PJT is boutique IB — ask RBA contacts for intros.",
    ),
    UpcomingProgram(
        firm="Centerview Partners",
        title="Campus Discovery",
        division="IB",
        url="https://www.centerviewpartners.com/careers/",
        opens_month=8,
        networking_tip="Centerview is elite IB — start coffee chats 2 months early.",
    ),
    UpcomingProgram(
        firm="SEO Career",
        title="SEO EDGE / SEO Alternative (Markets)",
        division="S&T",
        url="https://www.seo-usa.org/our-programs/",
        opens_month=1,
        opens_day=1,
        diversity="Diversity",
        networking_tip="You're already in SEO EDGE — ask your cohort about bank timelines.",
    ),
    UpcomingProgram(
        firm="BLK Capital Management",
        title="Wall Street Club (IB / PE)",
        division="IB",
        url="https://blkcapitalmanagement.org/",
        opens_month=4,
        diversity="Black",
        networking_tip=(
            "15-week rising-sophomore IB/PE program. Apply by emailing resume to "
            "wallstreetclub@blkcapitalmanagement.org (2026–27 deadline was June 1). "
            "Also watch Onuoha Fellowship and general BLK membership apps."
        ),
    ),
)
# Bulge bracket + elite boutiques + large AM — sophomore / discovery program pages.
CURATED_PROGRAMS = (
    # --- Bulge bracket ---
    {
        "firm": "Goldman Sachs",
        "title": "Possibilities / Sophomore Externship / Discovery",
        "division": "IB",
        "url": "https://www.goldmansachs.com/careers/students/programs",
        "typical_open": "August-September",
    },
    {
        "firm": "JPMorgan",
        "title": "Hispanic & Latino / Black Markets Fellowship",
        "division": "S&T",
        "url": "https://careers.jpmorgan.com/us/en/students/programs",
        "typical_open": "August-September",
    },
    {
        "firm": "JPMorgan",
        "title": "Launching Leaders / Advancing Black Pathways",
        "division": "IB",
        "url": "https://careers.jpmorgan.com/us/en/students/programs",
        "typical_open": "August-September",
    },
    {
        "firm": "JPMorgan",
        "title": "Global Markets / Finance Rotation",
        "division": "S&T",
        "url": "https://careers.jpmorgan.com/us/en/students/programs/global-markets",
        "typical_open": "August-September",
    },
    {
        "firm": "JPMorgan",
        "title": "Investment Banking Discovery / Campus",
        "division": "IB",
        "url": "https://careers.jpmorgan.com/us/en/students/programs",
        "typical_open": "August-September",
    },
    {
        "firm": "Morgan Stanley",
        "title": "Early Insights / Discovery / Campus Programs",
        "division": "IB",
        "url": "https://www.morganstanley.com/people-opportunities/students-graduates",
        "typical_open": "August-September",
    },
    {
        "firm": "Bank of America",
        "title": "Campus Discovery / Student Leaders",
        "division": "IB",
        "url": "https://campus.bankofamerica.com/",
        "typical_open": "August-September",
    },
    {
        "firm": "Citi",
        "title": "Early Insight / Freshman & Sophomore Programs",
        "division": "IB",
        "url": "https://jobs.citi.com/",
        "typical_open": "August-September",
    },
    {
        "firm": "Wells Fargo",
        "title": "Campus / Commercial Banking Programs",
        "division": "IB",
        "url": "https://www.wellsfargojobs.com/en/student-programs",
        "typical_open": "August-September",
    },
    {
        "firm": "Barclays",
        "title": "Discovery / Springboard / Markets Insight",
        "division": "S&T",
        "url": "https://search.jobs.barclays/",
        "typical_open": "August-September",
    },
    {
        "firm": "UBS",
        "title": "Campus Programs / Discovery",
        "division": "IB",
        "url": "https://www.ubs.com/global/en/careers/students-and-graduates.html",
        "typical_open": "August-September",
    },
    {
        "firm": "Deutsche Bank",
        "title": "Campus / dbAchieve / Sophomore Discovery",
        "division": "IB",
        "url": "https://careers.db.com/students-graduates",
        "typical_open": "August-September",
    },
    # --- Elite boutiques (IB / advisory) ---
    {
        "firm": "Evercore",
        "title": "Sophomore / Underclassman Programs",
        "division": "IB",
        "url": "https://www.evercore.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "Moelis",
        "title": "Underclassman / Campus Programs",
        "division": "IB",
        "url": "https://www.moelis.com/",
        "typical_open": "August-September",
    },
    {
        "firm": "Houlihan Lokey",
        "title": "Campus Programs / Discovery",
        "division": "IB",
        "url": "https://hl.com/careers/students/",
        "typical_open": "August-September",
    },
    {
        "firm": "Lazard",
        "title": "Diversity / Discovery / Campus",
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
        "firm": "Centerview Partners",
        "title": "Campus / Discovery Programs",
        "division": "IB",
        "url": "https://www.centerviewpartners.com/careers/",
        "typical_open": "August-September",
    },
    {
        "firm": "Perella Weinberg",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://pwpartners.com/careers/",
        "typical_open": "August-September",
    },
    {
        "firm": "Guggenheim Securities",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.guggenheimpartners.com/firm/careers",
        "typical_open": "August-September",
    },
    {
        "firm": "Greenhill",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.greenhill.com/en/careers",
        "typical_open": "August-September",
    },
    {
        "firm": "Rothschild & Co",
        "title": "Campus Programs",
        "division": "IB",
        "url": "https://www.rothschildandco.com/en/careers/",
        "typical_open": "August-September",
    },
    {
        "firm": "Jefferies",
        "title": "Campus / Discovery Programs",
        "division": "IB",
        "url": "https://www.jefferies.com/Careers/Students",
        "typical_open": "August-September",
    },
    # --- Asset management (AM track) ---
    {
        "firm": "BlackRock",
        "title": "Sophomore / Discovery / Founders",
        "division": "AM",
        "url": "https://careers.blackrock.com/students-and-graduates",
        "typical_open": "August-September",
    },
    {
        "firm": "PIMCO",
        "title": "Career Discovery / Campus",
        "division": "AM",
        "url": "https://careers.pimco.com/careers/students-graduates",
        "typical_open": "August-September",
    },
    {
        "firm": "Vanguard",
        "title": "College Internship / Discovery",
        "division": "AM",
        "url": "https://www.vanguardjobs.com/",
        "typical_open": "August-September",
    },
    # --- Dallas-heavy / diversity pipelines (profile fit) ---
    {
        "firm": "SEO Career",
        "title": "SEO EDGE / SEO Alternative",
        "division": "S&T",
        "url": "https://www.seo-usa.org/our-programs/",
        "typical_open": "Year-round",
    },
    {
        "firm": "Project Destined",
        "title": "Real Estate PE Training (Dallas)",
        "division": "AM",
        "url": "https://www.projectdestined.com/",
        "typical_open": "Spring-Summer",
    },
    {
        "firm": "BLK Capital Management",
        "title": "Wall Street Club / Onuoha Fellowship",
        "division": "IB",
        "url": "https://blkcapitalmanagement.org/",
        "typical_open": "Spring (WSC ~April–June)",
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
    "pjtpartners", "barclays", "ubs.com", "blackrock", "pimco", "vanguard",
    "deutschebank", "centerview", "pwpartners", "guggenheim", "greenhill",
    "rothschild", "jefferies", "houlihanlokey", "seo-usa", "projectdestined",
    "blkcapitalmanagement", "blk capital",
    "campus", "recruiting", "talent", "university", "college",
)
EMAIL_RECRUITING_SUBJECT = (
    "internship", "intern ", "summer analyst", "discovery", "sophomore",
    "freshman", "early insight", "campus", "application", "recruiting", "career",
    "global markets", "investment banking", "asset management", "finance rotation",
    "dallas", "dfw", "texas", "new york", "nyc", "possibilities", "externship",
    "class of 2029", "2028 summer", "seo", "rotation", "hispanic", "latino",
    "black", "fellowship", "pathways", "launching leaders",
)


def next_open_date(prog: UpcomingProgram, today: date | None = None) -> date:
    """Next calendar date this program is expected to open."""
    today = today or date.today()
    year = today.year
    target = date(year, prog.opens_month, prog.opens_day)
    if target < today:
        target = date(year + 1, prog.opens_month, prog.opens_day)
    return target


def days_until_open(prog: UpcomingProgram, today: date | None = None) -> int:
    today = today or date.today()
    return (next_open_date(prog, today) - today).days


def upcoming_early_alerts(
    today: date | None = None,
) -> list[tuple[JobPosting, str]]:
    """Programs due for a 1–2 month heads-up Telegram alert."""
    today = today or date.today()
    year = next_open_date(UPCOMING_PROGRAMS[0], today).year if UPCOMING_PROGRAMS else today.year
    alerts: list[tuple[JobPosting, str]] = []

    for prog in UPCOMING_PROGRAMS:
        if is_excluded(JobPosting(title=prog.title, firm=prog.firm, url=prog.url)):
            continue
        days = days_until_open(prog, today)
        for lo, hi, label in EARLY_ALERT_WINDOWS:
            if lo <= days <= hi:
                open_date = next_open_date(prog, today)
                notes = (
                    f"HEADS UP: opens ~{open_date.strftime('%b %d, %Y')} "
                    f"({days} days). {label} out — start networking now."
                )
                if prog.diversity:
                    notes += f" Diversity: {prog.diversity}."
                if prog.networking_tip:
                    notes += f" Tip: {prog.networking_tip}"

                posting = JobPosting(
                    title=prog.title,
                    firm=prog.firm,
                    url=prog.url,
                    location=prog.locations,
                    division=prog.division,
                    class_year="Sophomore",
                    program_type="Discovery",
                    source="Early alert",
                    notes=notes,
                    alert_window=label,
                )
                alerts.append((posting, posting.alert_dedupe_key(open_date.year, label)))
                break
    return alerts


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
            nyc_hit = any(k in text.lower() for k in NYC_KEYWORDS)
            soph_hit = any(k in text.lower() for k in SOPHOMORE_KEYWORDS)
            intern_hit = any(k in text.lower() for k in INTERNSHIP_KEYWORDS)
            div_hit = any(k in text.lower() for k in DIVERSITY_KEYWORDS)
            if is_excluded(JobPosting(
                title=prog["title"], firm=prog["firm"], url=prog["url"],
                raw_text=text[:2000],
            )):
                continue
            loc_parts = []
            if dallas_hit:
                loc_parts.append("Dallas")
            if nyc_hit:
                loc_parts.append("NYC")
            location = " / ".join(loc_parts) if loc_parts else "National"
            notes = (
                f"Page status: {status}. Typical open: {prog['typical_open']}."
            )
            if status == "Open" or (discovery_season_active() and (soph_hit or intern_hit or div_hit)):
                postings.append(JobPosting(
                    title=prog["title"],
                    firm=prog["firm"],
                    url=prog["url"],
                    location=location,
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
        if not any(tok in blob for tok in INTERNSHIP_KEYWORDS + SOPHOMORE_KEYWORDS + DIVERSITY_KEYWORDS):
            continue
        if is_excluded(JobPosting(
            title=em.get("subject", ""), firm=em.get("from", ""), url="", raw_text=blob,
        )):
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
        if is_excluded(copy):
            continue
        if is_relevant(
            copy,
            locations=locations,
            divisions=divisions,
            class_years=class_years,
            discovery_season=season,
        ):
            matched.append(copy)
    return matched
