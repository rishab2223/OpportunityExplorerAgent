"""Indeed job pages: start without asking.

Everything after the first page is the generic loop's, which already takes
Indeed's own form (smartapply.indeed.com) and clicks "Apply with Indeed" on
the job page. What it lacked was a start: a job page that is not LinkedIn got
"Ready to start? Type done when the page has loaded", and on a real session
that question sat unanswered for three minutes with the Apply button in plain
view - the candidate waiting for the agent to press it, the agent waiting for
them to type done.

The button itself is still pressed by the loop, not here. Which control is the
job's own Apply on an Indeed page - rather than one on a "similar jobs" card -
wants a real page to decide from, and none has been captured yet.
"""
from __future__ import annotations

from urllib.parse import urlparse

from src.apply import browser

READY_TIMEOUT = 10000
READY_STEP = 250


def start(page, sess) -> str:
    """Wait for the job page to show an Apply control and say what was seen:
    the control's text, or '' when none showed within READY_TIMEOUT."""
    if urlparse(page.url or "").netloc.lower().startswith("smartapply."):
        return ""                         # already on Indeed's own form
    waited = 0
    while True:
        seen = browser.apply_control(page)
        if seen or waited >= READY_TIMEOUT:
            break
        page.wait_for_timeout(READY_STEP)
        waited += READY_STEP
    if seen:
        sess.log(f"[indeed] The job page has loaded ('{seen}' is on it).")
    else:
        sess.log("[indeed] No Apply button showed within "
                 f"{READY_TIMEOUT // 1000}s - reading the page as it stands.")
    return seen
