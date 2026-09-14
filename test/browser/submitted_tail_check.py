"""A confirmation below the fold must still be seen.

A real session sat at "I am not making progress on this form" while the page
behind it said "Your application has been submitted." The wording matches
SUBMITTED_PAGE_RE, so the detector was never given it: page_text truncates at
2500 characters, and on that form the message was at 6872.

Runs against two pages:
  - a synthetic long form with the confirmation at the end;
  - the candidate's own dumped page, when it is still on disk. That dump holds
    real contact details, so it is read here and never copied anywhere.

Usage: python submitted_tail_check.py [path-to-dump-dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = E2E = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402
from src.apply.worker import _looks_submitted  # noqa: E402

pages = [("synthetic long form", (E2E / "fixture_submitted_tail.html").as_uri())]
dump = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    ROOT / "outputs" / "dom" / "20260830T161516_20260914-012836"
if (dump / "page.html").exists():
    pages.append(("the candidate's dumped page", (dump / "page.html").as_uri()))
else:
    print(f"  (no dump at {dump}; checking the synthetic page only)")

problems = []
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    # A saved page still points at the site's scripts, fonts and trackers, and
    # waiting on them hangs the load. Nothing outside the file is needed to
    # read its text - and nothing here should be talking to the employer.
    page.route("**/*", lambda route: route.continue_()
               if route.request.url.startswith("file:") else route.abort())
    for name, url in pages:
        # "commit" only: a saved page's blocked scripts can keep
        # domcontentloaded from ever firing, and the text is there regardless.
        try:
            page.goto(url, wait_until="commit", timeout=15000)
        except Exception as exc:
            print(f"  {name}: could not load ({str(exc).splitlines()[0][:70]})")
            problems.append(f"{name}: did not load")
            continue
        page.wait_for_timeout(700)
        head = browser.page_text(page)              # what the check used to get
        whole = browser.full_page_text(page)
        offset = whole.lower().find("application has been submitted")
        print(f"\n  {name}")
        print(f"    visible text     : {len(whole)} chars")
        print(f"    confirmation at  : {offset}")
        print(f"    seen in head 2500: {_looks_submitted(head)}")
        print(f"    seen in full text: {_looks_submitted(whole)}")
        if offset < 0:
            problems.append(f"{name}: the page does not carry the confirmation at all")
            continue
        if not _looks_submitted(whole):
            problems.append(f"{name}: still not detected with the full text")
        if offset > 2500 and _looks_submitted(head):
            problems.append(f"{name}: head window unexpectedly contained it")
    b.close()

print("\nPROBLEMS:" if problems else "\nDETECTED: the confirmation is seen wherever it sits.")
for p in problems:
    print("  -", p)
sys.exit(1 if problems else 0)
