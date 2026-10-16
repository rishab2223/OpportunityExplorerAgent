"""Is a waiting job still taking applications? (#38, Oct 2026)

About half the jobs acted on in the first two big runs had closed by the time
Start apply opened them (103 closed against 89 applied), and 101 of those had
already had a resume tailored. So once a day the shortlist's waiting jobs -
nothing done with them yet - are opened again, every few days each, and a
dead one leaves the shortlist before it costs anything more.

What a page says decides it, the same way an apply session reads it:
  - LinkedIn's own banner ("No longer accepting applications"), or its bounce
    to the search page: recorded closed, like the apply flow does without
    asking;
  - anywhere else, closed or not-found wording: marked "looks closed" for you
    to confirm with the row's Closed button - an employer's page is worded a
    thousand ways, and a wrong guess would hide a live job;
  - a sign-in wall or a "verify you are human" check: left alone and tried
    again next time. Never solved, never worked around.

Pages are opened one at a time, seconds apart, in a headless browser of its
own - not the apply sessions' Chrome profile, which may be in use.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src import db, history
from src.web import runs

OPEN, CLOSED, LOOKS_CLOSED, UNKNOWN = "open", "closed", "looks_closed", "unknown"
# How long a page gets to draw its text after it loads: LinkedIn and most
# career sites build the posting client-side.
SETTLE_MS = 2500


def _parse_stamp(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def last_checks() -> dict[str, dict[str, Any]]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT job_id, stamp, result, detail, checked_at FROM open_checks").fetchall()
    finally:
        conn.close()
    return {r["job_id"]: dict(r) for r in rows}


def record(job: dict[str, Any], stamp: str, result: str, detail: str = "") -> None:
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO open_checks (job_id, stamp, result, detail, checked_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET stamp = excluded.stamp, result = excluded.result, "
                "detail = excluded.detail, checked_at = excluded.checked_at",
                (str(job.get("job_id") or ""), stamp, result, detail[:300],
                 datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()


def waiting_jobs() -> list[dict[str, Any]]:
    """Every shortlisted job nothing has been done with, from every run, once
    each (the newest copy), with the run it is in as `stamp`."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stamp in runs.list_stamps():                    # newest first
        try:
            rows = runs.load_jobs(stamp)
        except (runs.RunNotFound, OSError, ValueError):
            continue
        for row in rows:
            if row.get("held_back"):
                continue
            keys = {k for k in (row.get("job_id") or "",
                                history.fingerprint(row.get("company") or "", row.get("title") or "")) if k}
            if keys & seen:
                continue
            seen |= keys
            if row.get("history_status") or row.get("decision"):
                continue
            row["stamp"] = stamp
            out.append(row)
    return out


def due(jobs: list[dict[str, Any]], checks: dict[str, dict[str, Any]], every_days: int,
        now: datetime | None = None) -> list[dict[str, Any]]:
    """The jobs to look at now: found at least `every_days` ago and not
    looked at for as long - so each is seen on day 2, 4, 6... while it waits.
    Never looked at first, then the longest unchecked."""
    now = now or datetime.now(timezone.utc)
    gap = timedelta(days=every_days)
    picked = []
    for job in jobs:
        found = _parse_stamp(str(job.get("stamp") or ""))
        if found is None or now - found < gap:
            continue
        last = checks.get(str(job.get("job_id") or ""))
        if last:
            try:
                checked = datetime.fromisoformat(last["checked_at"])
            except (TypeError, ValueError):
                checked = None
            if checked is not None and now - checked < gap:
                continue
            if last.get("result") == LOOKS_CLOSED:
                continue        # waiting for you to confirm; not opened again
            job["_last_checked"] = last["checked_at"]
        picked.append(job)
    picked.sort(key=lambda j: j.get("_last_checked") or "")
    return picked


def address(job: dict[str, Any]) -> str:
    """Where to look. A LinkedIn job: its LinkedIn page, which carries the
    closed banner. Anything else: the employer's own page when there is one -
    Indeed's own job pages turn headless browsers away."""
    listing = str(job.get("listing_url") or "")
    apply = str(job.get("apply_url") or "")
    if "linkedin.com" in listing:
        return listing
    if apply and "indeed." not in apply:
        return apply
    return listing or apply


def read_page(page, url: str) -> tuple[str, str]:
    """(result, detail) for one posting's page."""
    from src.apply import browser, worker
    from src.apply.sites import linkedin

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        return UNKNOWN, f"the page did not open: {str(exc).splitlines()[0][:120]}"
    page.wait_for_timeout(SETTLE_MS)
    text = browser.full_page_text(page)
    landed = page.url
    # Recorded without asking only on LinkedIn's own page, whose wording is
    # known; the same words on an employer's page are a "looks closed".
    if "linkedin.com" in (url + landed).lower() and linkedin.is_closed(text, landed):
        return CLOSED, "LinkedIn says it is no longer accepting applications"
    if linkedin.LOGIN_URL_RE.search(landed):
        return UNKNOWN, "a sign-in wall"
    check = browser.human_check(page)
    if check:
        return UNKNOWN, f"a {check} human check - left alone"
    status = response.status if response is not None else 0
    if status in (404, 410):
        return LOOKS_CLOSED, f"the page answers {status} (not found)"
    if worker._closed_by_address(page):
        return LOOKS_CLOSED, "the link now lands on the company's board with an error"
    if worker._looks_closed(text):
        found = worker.CLOSED_PAGE_RE.search(text)
        return LOOKS_CLOSED, f"the page says: '{found.group(0)[:80]}'" if found else "closed wording"
    if worker._looks_not_found(page, text):
        return LOOKS_CLOSED, "the page says the job or page is not found"
    return OPEN, ""


def run(every_days: int = 2, max_checks: int = 60, gap_seconds: float = 4.0,
        log: Callable[[str], None] = print, stop: Callable[[], bool] | None = None,
        opener: Callable[[], Any] | None = None) -> dict[str, int]:
    """Look at the jobs that are due. Returns counts by result.

    `opener` makes the browser page to use (a test hands in its own); by
    default a headless Chromium of its own."""
    jobs = due(waiting_jobs(), last_checks(), every_days)
    counts = {OPEN: 0, CLOSED: 0, LOOKS_CLOSED: 0, UNKNOWN: 0}
    if not jobs:
        log(f"[open check] Nothing due: every waiting job was looked at in the last {every_days} day(s).")
        return counts
    todo = jobs[:max_checks]
    log(f"[open check] {len(todo)} waiting job(s) to look at"
        + (f" ({len(jobs) - len(todo)} more tomorrow)" if len(jobs) > len(todo) else "") + "…")
    pw = browser_ = None
    try:
        if opener is not None:
            page = opener()
        else:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser_ = pw.chromium.launch(headless=True)
            page = browser_.new_page()
        for n, job in enumerate(todo):
            if stop is not None and stop():
                log("[open check] Stopped.")
                break
            if n:
                time.sleep(gap_seconds)
            url = address(job)
            label = f"{job.get('company', '')} - {job.get('title', '')}"
            if not url:
                counts[UNKNOWN] += 1
                record(job, job["stamp"], UNKNOWN, "no link to look at")
                continue
            result, detail = read_page(page, url)
            counts[result] += 1
            record(job, job["stamp"], result, detail)
            if result == CLOSED:
                history.record(job, "closed", stamp=job["stamp"],
                               note=f"found closed by the daily check: {detail}")
                log(f"[open check] Closed: {label} ({detail})")
            elif result == LOOKS_CLOSED:
                log(f"[open check] Looks closed - confirm it with the row's Closed button: {label} ({detail})")
    finally:
        if browser_ is not None:
            browser_.close()
        if pw is not None:
            pw.stop()
    log(f"[open check] Done: {counts[OPEN]} open, {counts[CLOSED]} closed, "
        f"{counts[LOOKS_CLOSED]} look closed, {counts[UNKNOWN]} could not be read.")
    return counts
