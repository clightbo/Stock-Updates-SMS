"""Weekly interview pitches shared by market_summary and Obsidian sync."""

from __future__ import annotations

from datetime import datetime, timezone

# Same pitch Mon–Sun so you can rehearse it for coffee chats / interviews.
INTERVIEW_IDEAS = [
    (
        "LONG NVDA (AI infrastructure): Hyperscalers are still in a capex "
        "arms race; NVDA's data-center GPUs have pricing power and a wide "
        "CUDA moat. Catalyst: next earnings guide + Blackwell shipment ramp. "
        "Risk: export controls, customer concentration (MSFT/GOOG/META), "
        "multiple compression if growth slows."
    ),
    (
        "LONG MSFT (quality + AI monetization): Enterprise stickiness + "
        "Copilot upsell turns Office/Azure into an AI tax on business "
        "software. Catalyst: Azure growth re-acceleration, Copilot seat "
        "attach. Risk: slow enterprise adoption, antitrust, capex weighing "
        "on FCF near term."
    ),
    (
        "LONG XLE / energy (geopolitical supply risk): Middle East "
        "disruptions and OPEC discipline keep a floor under crude; majors "
        "trade below historical multiples with buybacks/dividends. Catalyst: "
        "Strait of Hormuz headlines, inventory draws. Risk: global recession "
        "crushing demand, surprise supply increase."
    ),
    (
        "PAIRS: LONG QQQ / SHORT DIA (tech vs old economy): Leadership "
        "stays in megacap tech while industrials lag on rates and slowing "
        "PMI. Catalyst: earnings dispersion, AI narrative. Risk: factor "
        "reversal if Fed cuts spark broad rally in cyclicals."
    ),
    (
        "LONG AAPL (cash machine + services): Hardware cycle is mature but "
        "high-margin Services and buybacks support EPS; Vision/AI features "
        "are optional upside. Catalyst: Services growth, capital return. "
        "Risk: China demand, App Store regulation, multiple already full."
    ),
    (
        "MACRO HEDGE: LONG TLT / duration if Fed pivot (or short if "
        "sticky inflation): 10Y near 4.5%+ prices in higher-for-longer; "
        "either inflation cools and bonds rally, or growth surprises and "
        "equities win. Catalyst: CPI/PCE/Fed speak. Risk: fiscal deficits "
        "keeping term premium elevated."
    ),
    (
        "LONG GLD (geopolitical + debasement hedge): Wars, sanctions, and "
        "central-bank buying support gold when real yields stall or dollar "
        "weakens. Catalyst: escalation headlines, Fed pause. Risk: strong "
        "dollar + rising real yields."
    ),
    (
        "CREDIT CAUTION (tighten HY exposure): Late-cycle: tight spreads "
        "don't compensate for recession risk if unemployment ticks up. "
        "Thesis: favor IG over HY, or put spreads on HYG. Catalyst: weak "
        "jobs/claims, rising defaults. Risk: soft landing + hunt for yield "
        "keeps spreads compressed."
    ),
]


def weekly_interview_idea() -> str:
    """Same pitch all week — easier to prep for interviews."""
    week = datetime.now(timezone.utc).isocalendar().week
    return INTERVIEW_IDEAS[week % len(INTERVIEW_IDEAS)]
