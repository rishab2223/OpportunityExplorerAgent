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
CLOSED_RE = re.compile(
    r"no longer accepting applications|this job is no longer available|"
    r"job (is|has been) closed|position has been filled",
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
    fields = browser.snapshot(page)
    buttons = [f for f in fields if _clickable(f)]

    # A logged-out job page shows Sign in AND an Apply button; clicking Apply
    # while logged out just bounces to the authwall, so log in first.
    if any(SIGN_IN_RE.search(_text(f)) for f in buttons):
        sess.log("[linkedin] LinkedIn wants a login first.")
        return "login"

    if is_closed(browser.page_text(page, 4000), page.url):
        sess.log("[linkedin] This job is no longer accepting applications.")
        return "closed"

    target, kind = pick_apply(buttons)
    if target is None:
        sess.log("[linkedin] No apply button found on this page.")
        return ""
    browser.locate(page, target["id"]).click(timeout=15000)
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
