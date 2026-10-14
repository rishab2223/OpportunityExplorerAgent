"""A company page embedding Greenhouse in a frame: after the submit only the
frame changes, to "Thank you for applying" / "Thanks for taking the time to
apply to MongoDB!". The session sat waiting on that page (Oct 2026), because
only the host page and the form's frame were read, and the frame no longer
held a form.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.goto((HERE / "fixture_embed_host.html").as_uri())
    page.wait_for_timeout(300)

    print("== the form, before the submit ==")
    fields = browser.snapshot(page)
    check("the form in the frame is read", any(f.get("label") == "First Name*" for f in fields),
          str([f.get("label") for f in fields]))
    holder: dict = {}
    worker._take_submitted_baseline(page, fields, holder)
    check("nothing reads as submitted yet", worker._newly_submitted(page, holder) == "",
          worker._newly_submitted(page, holder))

    print("\n== the submit: only the frame changes ==")
    frame = next(f for f in page.frames if f is not page.main_frame)
    frame.locator("button").click()
    page.wait_for_timeout(500)
    # The loop reads the page again first; with no form left in the frame,
    # the page itself becomes the document read.
    browser.snapshot(page)
    said = worker._newly_submitted(page, holder)
    check("the frame's confirmation is seen", "thank" in said, said)
    watch = {"url": page.url, "submitted_seen": holder["submitted_seen"], "handled": set(), "keys": set()}
    check("and ends a wait on the candidate", worker._page_grew(page.context, page, watch) == "submitted",
          watch.get("confirmation", ""))

    b.close()

print()
if problems:
    print("EMBEDDED CONFIRM CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("EMBEDDED CONFIRM CHECK PASSED")
