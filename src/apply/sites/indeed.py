"""Indeed job pages: open the application without asking, and without the model.

A job page that was not LinkedIn used to get "Ready to start? Type done when
the page has loaded", and on a real session that question sat unanswered for
three minutes with the Apply button in plain view - the candidate waiting for
the agent to press it, the agent waiting for "done". Once answered, the model
was then asked to find the button (9 s), because the job page's only boxes
are Indeed's own What / Where search.

The job's apply control is found by the ids Indeed gives it, read off two
saved job pages (Sep 18 2026), one of each kind:

  #indeedApplyButton               "Apply with Indeed" - Indeed's own form
  #applyButtonLinkContainer button "Apply on company site" - a new tab

Matching on the word "apply" instead would also take an Apply that belongs to
some other job on the page. Neither id there, and the loop reads the page as
it stands, as it always did.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from src.apply import browser
from src.apply.sites.linkedin import focus_employer_tab, open_tabs

READY_TIMEOUT = 10000
READY_STEP = 250

BUTTONS = (
    ("indeed", "#indeedApplyButton", "Apply with Indeed"),
    ("external", "#applyButtonLinkContainer button, #applyButtonLinkContainer a",
     "Apply on company site"),
)
# How long a click gets to open something, per attempt. A button can be on
# screen before the page has wired it up - on LinkedIn the first click did
# nothing in 25 of 26 real openings - so the first wait is short and the
# patience goes to the later attempts.
OPEN_TIMEOUTS = (1500, 2500, 4000)
OPEN_STEP = 100
# Indeed's own banner on a dead posting (Oct 2 2026): "This job has expired on
# Indeed" above the title, and no apply button anywhere. It used to cost the
# full READY_TIMEOUT waiting for a button, then a "type closed" question for
# a posting that could not be applied to whatever the answer was. Indeed's
# wording only: the loop's broader closed check still asks, because other
# sites' banners can be ambiguous.
EXPIRED_RE = re.compile(r"this job has expired on indeed", re.IGNORECASE)


def start(page, sess) -> str:
    """Open the job's application. Returns 'indeed' (Indeed's own form),
    'external' (the employer's site in a new tab), 'closed' when Indeed says
    the posting has expired, or '' when neither button showed or nothing
    opened - the loop then reads the page as it stands."""
    if urlparse(page.url or "").netloc.lower().startswith("smartapply."):
        return ""                         # already on Indeed's own form
    found = _wait_for_button(page)
    if found == "closed":
        sess.log("[indeed] This job has expired on Indeed.")
        return "closed"
    if found is None:
        seen = browser.apply_control(page)
        sess.log(f"[indeed] Neither of Indeed's apply buttons showed within "
                 f"{READY_TIMEOUT // 1000}s"
                 + (f" ('{seen}' is on the page)" if seen else "")
                 + " - reading the page as it stands.")
        return ""
    kind, selector, name = found
    sess.log(f"[indeed] The job page has loaded - clicking '{name}'.")
    opened = _click_until_open(page, selector, sess)
    if opened == "new tab":
        focus_employer_tab(page, sess)
    elif opened == "navigated":
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass                          # the loop polls for fields on its own
    if not opened:
        sess.log("[indeed] Clicked, but nothing opened that I could confirm - "
                 "reading the page as it stands.")
        return ""
    sess.log(f"[indeed] The application opened ({opened}).")
    return kind


def _wait_for_button(page):
    """(kind, selector, name) for the apply button once one is visible,
    'closed' once the expired banner is, or None."""
    waited = 0
    while True:
        if EXPIRED_RE.search(browser.page_text(page) or ""):
            return "closed"
        for kind, selector, name in BUTTONS:
            try:
                if page.locator(selector).first.is_visible():
                    return kind, selector, name
            except Exception:
                pass
        if waited >= READY_TIMEOUT:
            return None
        page.wait_for_timeout(READY_STEP)
        waited += READY_STEP


def _where(page) -> tuple[str, int]:
    parts = urlparse(page.url or "")
    return f"{parts.netloc}{parts.path}".lower(), open_tabs(page)


def _opened(page, before: tuple[str, int]) -> str:
    """'navigated' or 'new tab' once the click has done something, else ''.
    Host and path only: Indeed rewrites its own query string as the page
    settles, which is not a navigation."""
    now = _where(page)
    if now[0] != before[0]:
        return "navigated"
    return "new tab" if now[1] > before[1] else ""


def _click_until_open(page, selector: str, sess) -> str:
    before = _where(page)
    for attempt, window in enumerate(OPEN_TIMEOUTS):
        if attempt:
            # A first click that was merely slow can open while we get ready
            # to press again, and a second press would open it twice.
            late = _opened(page, before)
            if late:
                return late
        try:
            # A CSS selector, not a snapshot id: a button the page has rebuilt
            # is simply found again.
            browser.click(page.locator(selector).first, timeout=3000, fallback_timeout=1000)
        except Exception as exc:
            sess.log(f"[indeed] The apply click did not land: {str(exc).splitlines()[0][:100]}")
        waited = 0
        while waited < window:
            opened = _opened(page, before)
            if opened:
                return opened
            page.wait_for_timeout(OPEN_STEP)
            waited += OPEN_STEP
        if attempt + 1 < len(OPEN_TIMEOUTS):
            sess.log("[indeed] The apply button did not open anything yet; trying again.")
    return _opened(page, before)
