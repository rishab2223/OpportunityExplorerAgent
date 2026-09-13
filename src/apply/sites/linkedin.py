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


# One timeout per attempt, and the first one is deliberately short.
#
# Playwright's click waits for the element to hold still, and LinkedIn's job
# card is animating precisely when we first reach it - so attempt one is the
# attempt most likely to fail, and the least worth waiting on. Six seconds of
# stability wait plus three of fallback bought nothing and cost ten seconds
# before the retry that actually worked. Patience is spent later instead, on
# the attempts where the card has had time to settle and a slow click is
# plausibly a click that will land.
APPLY_CLICK_TIMEOUTS = (1200, 3000, 6000)
APPLY_CLICK_TRIES = len(APPLY_CLICK_TIMEOUTS)
# A node the card has already replaced never resolves, so the direct-dispatch
# fallback spends its whole budget and raises regardless.
APPLY_CLICK_FALLBACK = 1200


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


# A dialog LinkedIn had OPEN before we clicked is not evidence of anything, so
# every one of those is stamped and then ignored. Only an unstamped dialog can
# be the apply modal.
#
# Stamped through the same ":visible" rule the check uses, not through every
# [role=dialog] in the document: a site that ships its apply modal hidden in
# the markup and reveals it on click - which is the common way to build one -
# would otherwise have the modal stamped before it was ever opened, and the
# click could then never be confirmed at all.
PRE_DIALOG_ATTR = "data-oea-predialog"
OPEN_DIALOG_SELECTOR = "div[role=dialog]:visible"
STAMP_DIALOGS_JS = f"els => els.forEach(d => d.setAttribute('{PRE_DIALOG_ATTR}', '1'))"
NEW_DIALOG_SELECTOR = f"div[role=dialog]:not([{PRE_DIALOG_ATTR}]):visible"
# ...and being new is still not enough. LinkedIn mounts its own overlays late
# (the messaging bubble carries a search box, so "has a control" does not
# separate it either). The apply modal names itself: "Apply to <company>",
# "Contact info", "Submit application".
APPLY_DIALOG_RE = re.compile(
    r"\bapply\b|\bapplication\b|contact info|\bresume\b", re.IGNORECASE
)


def _url_key(url: str) -> str:
    """Host and path only. LinkedIn rewrites its own query string as the card
    hydrates - refId, trackingId, currentJobId - which is not a navigation and
    must not be read as one. Both real navigations away from a job page (the
    openSDUIApplyFlow page, an employer's site) change the host or the path."""
    text = (url or "").split("#", 1)[0].split("?", 1)[0]
    return text.rstrip("/").lower()


def _new_apply_dialog(page) -> bool:
    """A dialog that was not open before the click, and that reads like an
    apply form rather than one of LinkedIn's own overlays."""
    try:
        dialogs = page.locator(NEW_DIALOG_SELECTOR)
        for i in range(dialogs.count()):
            dialog = dialogs.nth(i)
            text = f"{dialog.get_attribute('aria-label') or ''} {dialog.inner_text() or ''}"
            if APPLY_DIALOG_RE.search(text):
                return True
    except Exception:
        return False
    return False


def _baseline(page) -> tuple[str, int]:
    """What the page looked like before the click, to compare against. Stamps
    the dialogs that are already open as a side effect."""
    try:
        page.locator(OPEN_DIALOG_SELECTOR).evaluate_all(STAMP_DIALOGS_JS)
    except Exception:
        pass
    return _url_key(page.url or ""), open_tabs(page)


def _flow_opened(page, before: tuple[str, int]) -> str:
    """Which way the apply flow opened, or '' if it did not.

    One signal per way LinkedIn opens one, each measured as a CHANGE from
    before the click, and each narrowed to changes only a click can cause:

      navigated  - host or path moved: the openSDUIApplyFlow page
      new tab    - "Apply on company website"
      dialog     - a dialog that was not open before AND names itself an
                   apply form

    Every previous version of this check answered "does the page look
    different", which the page answers yes to on its own. Dialogs counted by
    presence called a job page carrying a messaging overlay an open apply
    flow. A rise in form controls fired on LinkedIn's lazy panels rendering.
    Counting dialogs by increase still fired when an overlay mounted late, and
    comparing the whole URL still fired when LinkedIn appended its own
    tracking parameters. Each time the agent announced a form that was not
    there, and the model was paid to press the button it thought it had
    pressed.
    """
    before_url, before_tabs = before
    if _url_key(page.url or "") != before_url:
        return "navigated"
    if open_tabs(page) > before_tabs:
        return "new tab"
    return "dialog" if _new_apply_dialog(page) else ""


def _wait_for_flow(page, before: tuple[str, int],
                   timeout: int = APPLY_OPEN_TIMEOUT) -> str:
    """Give the click a moment to do something, and say what it did."""
    waited = 0
    while True:
        opened = _flow_opened(page, before)
        if opened or waited >= timeout:
            return opened
        page.wait_for_timeout(APPLY_OPEN_STEP)
        waited += APPLY_OPEN_STEP


def click_apply(page, sess) -> tuple[dict[str, Any] | None, str, str]:
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

    Returns the button, its kind, and HOW the flow opened ('navigated', 'new
    tab', 'dialog') or '' when the click was never confirmed. The caller must
    not describe the form as open on an empty third value.
    """
    before = _baseline(page)
    last_error: Exception | None = None
    target = kind = None
    for attempt in range(APPLY_CLICK_TRIES):
        target, kind = pick_apply([f for f in browser.snapshot(page) if _clickable(f)])
        if target is None:
            return None, "", ""
        raised = False
        try:
            browser.click(
                browser.locate(page, target["id"], str(target.get("elid") or "")),
                timeout=APPLY_CLICK_TIMEOUTS[attempt],
                fallback_timeout=APPLY_CLICK_FALLBACK,
            )
        except Exception as exc:
            last_error = exc
            raised = True
        budget = APPLY_OPEN_AFTER_ERROR if raised else APPLY_OPEN_TIMEOUT
        opened = _wait_for_flow(page, before, budget)
        if opened:
            # Which signal fired is worth a line in the transcript: three
            # versions of this check have now reported a form that was not
            # there, and a log that names the evidence is the only way to tell
            # a fourth one from a real open without watching the screen.
            sess.log(f"[linkedin] The apply flow opened ({opened}).")
            return target, kind, opened
        if attempt + 1 < APPLY_CLICK_TRIES:
            sess.log("[linkedin] The apply button did not open anything - the card was "
                     "still rendering; reading the page and trying again.")
            page.wait_for_timeout(500)
    if last_error is not None:
        raise last_error
    # Clicked with no error and nothing visibly opened. Hand back anyway: the
    # generic loop reads whatever is on screen, which beats raising here.
    return target, kind, ""


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
    if state == "apply":
        # Marks the boundary between LinkedIn's own loading and our clicking.
        # With elapsed times on every line, this is the difference between
        # "the site is slow" and "the agent is slow", which is not a question
        # anyone should have to answer by watching the screen.
        sess.log("[linkedin] The job card has finished loading.")

    target, kind, opened = click_apply(page, sess)
    if target is None:
        sess.log("[linkedin] No apply button found on this page.")
        return ""
    if kind == "easy_apply":
        # One wait, not two, and each one earned by what actually opened. The
        # modal's shell (title, close button, spinner) arrives before its
        # fields, and reading the page in between scoped the snapshot to the
        # job page BEHIND the modal - so wait for the fields, but ONLY when
        # there is a modal to wait for. Hunting a dialog that was never there
        # cost eight seconds and taught us nothing.
        if opened == "dialog":
            try:
                page.wait_for_selector(
                    f"{NEW_DIALOG_SELECTOR} :is(input, select, textarea)", timeout=8000
                )
            except Exception:
                pass
            page.wait_for_timeout(400)
        elif opened:
            page.wait_for_timeout(600)
        if opened:
            sess.log("[linkedin] Easy Apply - the application form is open.")
        else:
            # Say what is true. Announcing an open form that is not open sent
            # the model a job page and had it press the button for us, which
            # is the slowest possible way to click Easy Apply.
            sess.log("[linkedin] Clicked Easy Apply, but nothing opened that I could "
                     "confirm - reading the page as it stands.")
    else:
        page.wait_for_timeout(3000)
        sess.log("[linkedin] External apply - following the employer's site.")
    return kind
