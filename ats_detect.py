"""ATS / careers-portal detector — a deterministic classifier (no network, no LLM, no state).

Given a URL or email, return the applicant-tracking-system / job-portal it belongs to (a human
label), or None. /daily-agent uses this to recognise when a company only accepts applications
through a portal: in that case a cold email to a generic inbox is usually futile, so the agent
routes the lead to Zineb to apply through the portal instead of burning one of the 2 daily cold
slots. Reasoning stays in the skill; this just recognises the hosts.
"""
from __future__ import annotations

# host substring -> human label. Big global ATSs + the ones common for French startups/SMEs.
_ATS = {
    "jobs.lever.co": "Lever", "lever.co": "Lever",
    "boards.greenhouse.io": "Greenhouse", "greenhouse.io": "Greenhouse",
    "job-boards.greenhouse.io": "Greenhouse",
    "apply.workable.com": "Workable", "workable.com": "Workable",
    "jobs.ashbyhq.com": "Ashby", "ashbyhq.com": "Ashby",
    "smartrecruiters.com": "SmartRecruiters", "recruitee.com": "Recruitee",
    "teamtailor.com": "Teamtailor", "personio.": "Personio", "join.com": "Join",
    "welcomekit.co": "Welcome to the Jungle", "welcometothejungle.com": "Welcome to the Jungle",
    "taleez.com": "Taleez", "flatchr.io": "Flatchr", "softy.pro": "Softy",
    "digitalrecruiters.com": "DigitalRecruiters", "factorialhr.com": "Factorial",
    "jobvite.com": "Jobvite", "icims.com": "iCIMS", "myworkdayjobs.com": "Workday",
    "workday": "Workday", "taleo.net": "Taleo", "successfactors": "SuccessFactors",
    "bamboohr.com": "BambooHR", "pinpointhq.com": "Pinpoint", "zohorecruit": "Zoho Recruit",
    "eu.dol.jobs": "beetween", "beetween.com": "Beetween", "cadremploi": "Cadremploi",
}


def detect(url_or_email: str) -> str | None:
    """Return the ATS/portal name if the URL/email is hosted on one, else None."""
    s = (url_or_email or "").strip().lower()
    if not s:
        return None
    for needle, label in _ATS.items():
        if needle in s:
            return label
    return None


def is_portal(url_or_email: str) -> bool:
    return detect(url_or_email) is not None
