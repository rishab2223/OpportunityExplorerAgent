"""Per-site apply handlers.

A handler's job is navigation, not form-filling: get from a job listing to a
page with a real form, then hand back to the generic loop (resolver + LLM).
detect() keys off the hostname; add an entry here when the next source
(Indeed, Workday, ...) gets its own handler.
"""

from __future__ import annotations

from urllib.parse import urlparse


# Which applicant tracking system a form belongs to. Only LinkedIn has a
# handler; the rest are named so the form catalogue has something to file a
# page under, and so a log line can say which system you are looking at.
ATS_HOSTS = (
    ("linkedin", (".linkedin.com",)),
    ("workday", (".myworkdayjobs.com", ".myworkday.com", ".workday.com")),
    ("phenom", (".phenompeople.com", ".phenomapp.com")),
    ("greenhouse", (".greenhouse.io", ".boards.greenhouse.io")),
    ("lever", (".lever.co",)),
    ("talentrecruit", (".talentrecruit.com",)),
    ("successfactors", (".successfactors.com", ".sapsf.com")),
    ("smartrecruiters", (".smartrecruiters.com",)),
    ("icims", (".icims.com",)),
    ("taleo", (".taleo.net",)),
    ("ashby", (".ashbyhq.com",)),
)


def detect(url: str) -> str:
    """'linkedin' for LinkedIn job pages, '' for everything else.

    Only names a site that has a HANDLER here, so the apply loop's branch on
    this value is unchanged. Use ats() to ask which system a page belongs to.
    """
    return "linkedin" if ats(url) == "linkedin" else ""


def ats(url: str) -> str:
    """Which applicant tracking system this URL belongs to, or ''. Unlike
    detect() this names systems with no handler of their own: it is what the
    form catalogue files a page under."""
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    if not host:
        return ""
    host = host.split(":")[0]
    for name, suffixes in ATS_HOSTS:
        for suffix in suffixes:
            if host == suffix.lstrip(".") or host.endswith(suffix):
                return name
    return ""
