"""The daily "still open?" check reads a page the way an apply session would
(#38, Oct 2026). Every address here is answered by the check itself
(page.route), so nothing reaches a real site."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

from src import open_check  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


def html(body: str) -> str:
    return f"<!doctype html><html><head><title>Job</title></head><body>{body}</body></html>"


PAGES = {
    "https://www.linkedin.com/jobs/view/1/": (200, html("<h1>Backend Engineer</h1><p>No longer accepting applications</p>")),
    "https://www.linkedin.com/jobs/view/2/": (200, html("<h1>Backend Engineer</h1><button>Easy Apply</button>"
                                                        "<p>Build services on AWS.</p>")),
    "https://careers.example.test/job/3": (200, html("<h1>Engineer</h1><p>This position has been filled.</p>")),
    "https://careers.example.test/job/4": (404, html("<h1>Not here</h1>")),
    "https://careers.example.test/job/5": (200, html("<h1>Engineer</h1><p>We are hiring. Requirements: Node.js.</p>"
                                                     "<a href='#'>Apply now</a>")),
    "https://careers.example.test/job/6": (200, html("<p>Verify you are human by completing the action below.</p>")),
    "https://careers.example.test/job/7": (200, html("<h1>Engineer</h1><p>We are no longer accepting applications "
                                                     "for this role.</p>")),
    "https://www.linkedin.com/authwall?trk=x": (200, html("<h1>Sign in</h1>")),
}


def serve(route):
    url = route.request.url
    if url.startswith("https://www.linkedin.com/jobs/view/8"):
        route.fulfill(status=302, headers={"Location": "https://www.linkedin.com/authwall?trk=x"})
        return
    status, body = PAGES.get(url, (404, html("unknown")))
    route.fulfill(status=status, content_type="text/html", body=body)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.route("**/*", serve)
    want = [
        ("LinkedIn's closed banner is recorded closed", "https://www.linkedin.com/jobs/view/1/", "closed"),
        ("a live LinkedIn posting is open", "https://www.linkedin.com/jobs/view/2/", "open"),
        ("an employer's 'position has been filled' looks closed (yours to confirm)",
         "https://careers.example.test/job/3", "looks_closed"),
        ("a 404 looks closed", "https://careers.example.test/job/4", "looks_closed"),
        ("a live employer page is open", "https://careers.example.test/job/5", "open"),
        ("a human check is left alone", "https://careers.example.test/job/6", "unknown"),
        ("LinkedIn's wording on an employer's page is NOT recorded closed without you",
         "https://careers.example.test/job/7", "looks_closed"),
        ("a LinkedIn sign-in wall is left alone", "https://www.linkedin.com/jobs/view/8/", "unknown"),
    ]
    for label, url, expected in want:
        result, detail = open_check.read_page(page, url)
        check(label, result == expected, f"{result}: {detail}")
    b.close()

print()
if problems:
    print("OPEN CHECK CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("OPEN CHECK CHECK PASSED")
