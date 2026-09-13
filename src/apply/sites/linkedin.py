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


APPLY_OPEN_TIMEOUT = 4000
# A click that already raised probably never landed, so confirm it briefly
# and get on with the retry; only a clean click earns the full window.
APPLY_OPEN_AFTER_ERROR = 1000
APPLY_OPEN_STEP = 250


def visible_dialogs(page) -> int:
    try:
        return page.locator("div[role=dialog]:visible").count()
    except Exception:
        return 0


def open_tabs(page) -> int:
    try:
        return len([p for p in page.context.pages if not p.is_closed()])
    except Exception:
        return 1


def _baseline(page) -> tuple[str, int, int]:
    """What the page looked like before the click, to compare against."""
    return page.url or "", visible_dialogs(page), open_tabs(page)


def _flow_opened(page, before: tuple[str, int, int]) -> bool:
    """Did the click actually open an apply flow?

    One signal per way LinkedIn opens one, and every one is measured as a
    CHANGE from before the click:

      the URL moved        - the openSDUIApplyFlow page, which is a navigation
      a new tab appeared   - "Apply on company website"
      one more visible dialog than before - the Easy Apply modal

    Nothing here is "the page looks different", which is what the previous
    version tested and why it lied. Counting dialogs by presence called a job
    page that already had a visible overlay an open apply flow; counting a
    rise in form controls fired on LinkedIn's own lazy panels finishing their
    render, with no click involved at all. Both reported success, start()
    then spent eight seconds hunting a dialog that was never there, and the
    model was paid to press the button the agent thought it had pressed.
    """
    before_url, before_dialogs, before_tabs = before
    if (page.url or "") != before_url:
        return True
    if open_tabs(page) > before_tabs:
        return True
    return visible_dialogs(page) > before_dialogs


def _wait_for_flow(page, before: tuple[str, int, int],
                   timeout: int = APPLY_OPEN_TIMEOUT) -> bool:
    """Give the click a moment to do something, and say whether it did."""
    waited = 0
    while True:
        if _flow_opened(page, before):
            return True
        if waited >= timeout:
            return False
        page.wait_for_timeout(APPLY_OPEN_STEP)
        waited += APPLY_OPEN_STEP


def click_apply(page, sess) -> tuple[dict[str, Any] | None, str]:
    """Click the job's apply control, re-reading the page between attempts.

    LinkedIn's job card animates while its panels load, and is rebuilt more
    than once. Playwright's click waits for an element to hold still, which
    that card does not, and then the node it was waiting on is replaced:
    "element is not stable", then "element was detached from the DOM". A real
    application died there after fifteen seconds with the Easy Apply button
    plainly on screen.

    Three things go wrong and all three are handled: an element that will not
    settle is clicked directly instead (browser.click does that, and the raw
    Playwright click used here before did not); each attempt takes a fresh
    snapshot, so a rebuilt button is found again rather than waited on; and
    the OUTCOME is what decides success, not the absence of an exception.

    That last one matters most. browser.click's fallback dispatches the click
    on the element itself, which succeeds quietly on a node React has already
    thrown away - nothing opens, nothing raises. A session then spent ten
    seconds waiting for a dialog and a whole model call being told to press
    the button it thought it had already pressed.
    """
    before = _baseline(page)
    last_error: Exception | None = None
    target = kind = None
    for attempt in range(APPLY_CLICK_TRIES):
        target, kind = pick_apply([f for f in browser.snapshot(page) if _clickable(f)])
        if target is None:
            return None, ""
        raised = False
        try:
            browser.click(
                browser.locate(page, target["id"], str(target.get("elid") or "")),
                timeout=APPLY_CLICK_TIMEOUT,
            )
        except Exception as exc:
            last_error = exc
            raised = True
        budget = APPLY_OPEN_AFTER_ERROR if raised else APPLY_OPEN_TIMEOUT
        if _wait_for_flow(page, before, budget):
            return target, kind
        if attempt + 1 < APPLY_CLICK_TRIES:
            sess.log("[linkedin] The apply button did not open anything - the card was "
                     "still rendering; reading the page and trying again.")
            page.wait_for_timeout(500)
    if last_error is not None:
        raise last_error
    # Clicked with no error and nothing visibly opened. Hand back anyway: the
    # generic loop reads whatever is on screen, which beats raising here.
    return target, kind


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
        # One wait, not two. click_apply has already confirmed that something
        # opened, so the only thing left to wait for is the step's fields: the
        # modal's shell (title, close button, spinner) arrives first, and
        # reading the page in between scoped the snapshot to the job page
        # BEHIND the modal. Waiting for the dialog and THEN for its fields
        # cost eleven seconds on a flow that is not marked as a dialog at all.
        if visible_dialogs(page):
            # There IS a modal: its shell (title, close button, spinner)
            # arrives before its fields, and reading in between scoped the
            # snapshot to the job page behind it.
            try:
                page.wait_for_selector(
                    "div[role=dialog] :is(input, select, textarea)", timeout=8000
                )
            except Exception:
                pass
            page.wait_for_timeout(400)
        else:
            # A flow that navigated instead of opening a modal. Hunting for a
            # dialog here burned eight seconds before the loop read the page.
            page.wait_for_timeout(600)
        sess.log("[linkedin] Easy Apply - the application form is open.")
    else:
        page.wait_for_timeout(3000)
        sess.log("[linkedin] External apply - following the employer's site.")
    return kind
