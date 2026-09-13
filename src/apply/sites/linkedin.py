"""LinkedIn job pages: get from the listing to a fillable form.

Two paths, decided by what the page offers:
- "Easy Apply" opens LinkedIn's own multi-step modal in the same tab; the
  generic loop then fills it (script advances the wizard; the user clicks the
  final Submit application themselves).
- "Apply" hands off to the employer's site, usually in a new tab, which
  browser.current_page() follows.

This module only clicks one button and reports which branch it took. It never
fills anything and never touches the final submit.
"""

from __future__ import annotations

import re
from typing import Any

from src.apply import browser

EASY_APPLY_RE = re.compile(r"\beasy apply\b", re.IGNORECASE)
APPLY_RE = re.compile(r"\bapply\b", re.IGNORECASE)
SIGN_IN_RE = re.compile(r"\b(sign in|join now)\b", re.IGNORECASE)
# Logged-out LinkedIn bounces clicks to these; never try to drive them.
LOGIN_URL_RE = re.compile(r"linkedin\.com/(authwall|uas/login|login|checkpoint)", re.IGNORECASE)
# Deliberately narrower than worker.CLOSED_PAGE_RE, and kept separate from it:
# a match here records the job closed and ends the session WITHOUT asking,
# so it may only hold wording LinkedIn itself uses. The broad pattern, which
# runs on any employer's page, asks the candidate to confirm first. Wording
# that belongs to both has to be added in both places.
CLOSED_RE = re.compile(
    r"no longer accepting applications|not (currently |presently )?accepting applications|"
    r"this job is no longer available|job (is|has been) closed|position has been filled|"
    # A posting that was taken down does not say "closed" at all: LinkedIn
    # shows "Unable to load the page - Job id provided may not be valid or
    # the job posting has been removed".
    r"job posting has been removed|job id provided may not be valid",
    re.IGNORECASE,
)


def is_closed(page_text: str, url: str) -> bool:
    """The posting stopped accepting applications: LinkedIn shows a banner and
    then bounces to the jobs search page."""
    if CLOSED_RE.search(page_text or ""):
        return True
    lowered = (url or "").lower()
    if "linkedin.com" not in lowered:
        return False
    # We navigated to /jobs/view/... - landing anywhere else on the jobs
    # section means LinkedIn redirected a dead posting away.
    return "/jobs/view/" not in lowered and ("/jobs/search" in lowered or "/jobs/collections" in lowered)


def _text(field: dict[str, Any]) -> str:
    return f"{field.get('text') or ''} {field.get('label') or ''}".strip()


def _clickable(field: dict[str, Any]) -> bool:
    return field.get("tag") in ("button", "a") or field.get("role") == "button"


# browser.launch navigates with wait_until="domcontentloaded", but LinkedIn
# builds the job card client-side after that, so a snapshot taken the moment
# the page "loads" can hold none of it.
READY_TIMEOUT = 10000
READY_STEP = 250


def page_state(fields: list[dict[str, Any]], page_text: str, url: str) -> str:
    """Which of the three things that decide what happens next this page is
    showing: 'login', 'closed', 'apply', or '' for none of them yet.

    Order matters. A logged-out job page shows an apply button AND a sign-in,
    and clicking apply there only bounces to the authwall, so login wins.
    """
    buttons = [f for f in fields if _clickable(f)]
    if any(SIGN_IN_RE.search(_text(f)) for f in buttons):
        return "login"
    if is_closed(page_text, url):
        return "closed"
    return "apply" if pick_apply(buttons)[0] is not None else ""


APPLY_CLICK_TIMEOUT = 6000
APPLY_CLICK_TRIES = 3


def _flow_opened(page, before_url: str) -> bool:
    """The apply flow is up: the URL moved, or a dialog became VISIBLE.

    Checked before any retry, so a click that landed and only THEN lost its
    element to a re-render is never sent a second time. Visibility is the
    whole point: a page that keeps its apply dialog in the DOM behind
    display:none has one from the moment it loads, and counting that as
    "opened" reports a click that never happened as a success.
    """
    if (page.url or "") != before_url:
        return True
    try:
        return page.locator("div[role=dialog]:visible").count() > 0
    except Exception:
        return False


def click_apply(page, sess) -> tuple[dict[str, Any] | None, str]:
    """Click the job's apply control, re-reading the page between attempts.

    LinkedIn's job card animates while its panels load, and is rebuilt more
    than once. Playwright's click waits for an element to hold still, which
    that card does not, and then the node it was waiting on is replaced:
    "element is not stable", then "element was detached from the DOM". A real
    application died there after fifteen seconds with the Easy Apply button
    plainly on screen.

    Two things go wrong and both are handled: an element that will not settle
    is clicked directly instead (browser.click does that, and the raw
    Playwright click used here before did not), and each attempt takes a
    fresh snapshot, so a rebuilt button is found again rather than waited on.
    """
    before_url = page.url or ""
    last_error: Exception | None = None
    for attempt in range(APPLY_CLICK_TRIES):
        target, kind = pick_apply([f for f in browser.snapshot(page) if _clickable(f)])
        if target is None:
            return None, ""
        try:
            browser.click(
                browser.locate(page, target["id"], str(target.get("elid") or "")),
                timeout=APPLY_CLICK_TIMEOUT,
            )
            return target, kind
        except Exception as exc:
            last_error = exc
            if _flow_opened(page, before_url):
                return target, kind          # it landed; the card moved on after
            if attempt + 1 < APPLY_CLICK_TRIES:
                sess.log("[linkedin] The apply button moved as the page was still "
                         "rendering; reading the page again.")
                page.wait_for_timeout(800)
    raise last_error if last_error is not None else RuntimeError("could not click apply")


def wait_for_page(page, timeout: int = READY_TIMEOUT) -> str:
    """Wait for the job page to show an apply control, a sign-in wall or a
    closed banner, and say which arrived. '' means none did in time.

    This replaces asking the candidate "type done when the page has loaded".
    That question put a human in front of something the page can be asked
    directly, and it was answered before the card had rendered about as often
    as after - while on a posting that was already closed, or one that needed
    a login, it made them press a key to be told so.
    """
    waited = 0
    while True:
        try:
            state = page_state(browser.snapshot(page),
                               browser.page_text(page, 4000), page.url or "")
        except Exception:
            state = ""
        if state or waited >= timeout:
            return state
        page.wait_for_timeout(READY_STEP)
        waited += READY_STEP


def pick_apply(buttons: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """The job's own apply control and its kind ('easy_apply' | 'external').

    Take the FIRST apply-ish button in DOM order and only then decide which
    kind it is: the top job card precedes the "Similar jobs" rail, so scanning
    the whole page for "Easy Apply" first could pick a different job's button.
    """
    for field in buttons:
        text = _text(field)
        if EASY_APPLY_RE.search(text):
            return field, "easy_apply"
        if APPLY_RE.search(text):
            return field, "external"
    return None, ""


def start(page, sess) -> str:
    """Open the apply flow for the LinkedIn job page. Returns
    'easy_apply', 'external', 'login', 'closed' or '' (nothing recognized)."""
    if LOGIN_URL_RE.search(page.url or ""):
        sess.log("[linkedin] LinkedIn wants a login first.")
        return "login"

    # Wait for the card rather than asking the candidate whether it has
    # arrived. A logged-out page shows Sign in AND an Apply button; clicking
    # Apply there only bounces to the authwall, so login wins.
    state = wait_for_page(page)
    if state == "login":
        sess.log("[linkedin] LinkedIn wants a login first.")
        return "login"
    if state == "closed":
        sess.log("[linkedin] This job is no longer accepting applications.")
        return "closed"

    target, kind = click_apply(page, sess)
    if target is None:
        sess.log("[linkedin] No apply button found on this page.")
        return ""
    if kind == "easy_apply":
        try:
            page.wait_for_selector("div[role=dialog]", timeout=10000)
        except Exception:
            page.wait_for_timeout(1500)
            sess.log("[linkedin] Clicked Easy Apply, but no dialog appeared yet.")
            return kind
        # The shell (title, close button, spinner) comes first; the step's
        # fields arrive a moment later. Reading the page before they exist
        # meant the snapshot scoped to the page BEHIND the modal.
        try:
            page.wait_for_selector(
                "div[role=dialog] :is(input, select, textarea)", timeout=10000
            )
            page.wait_for_timeout(500)  # let the rest of the step render
            sess.log("[linkedin] Easy Apply - opened the application modal.")
        except Exception:
            sess.log("[linkedin] Easy Apply modal is open, but its form has not loaded yet.")
    else:
        page.wait_for_timeout(3000)
        sess.log("[linkedin] External apply - following the employer's site.")
    return kind
