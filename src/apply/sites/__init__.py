"""Per-site apply handlers.

A handler's job is navigation, not form-filling: get from a job listing to a
page with a real form, then hand back to the generic loop (resolver + LLM).
detect() keys off the hostname; add an entry here when the next source
(Indeed, Workday, ...) gets its own handler.
"""

from __future__ import annotations

from urllib.parse import urlparse


def detect(url: str) -> str:
    """'linkedin' for LinkedIn job pages, '' for everything else."""
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    if host.endswith("linkedin.com"):
        return "linkedin"
    return ""
