"""From the Axionix Easy Apply session (Oct 2026), two delays:

  - LinkedIn's apply page showed its own "Easy Apply to this job" button
    before the form; the agent announced the form as open, and the planning
    model took 17 s to say "click it";
  - a Yes / No radio was rebuilt while it was being ticked, and every way of
    ticking it then waited out its own timeout on an element that was gone:
    16 s, then "could not be ticked (could not inspect it)".

Fixture pages only; nothing reaches LinkedIn.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402
from src.apply.sites import linkedin  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, line):
        self.lines.append(line)
        print("    LOG", line)


JOB = (HERE / "fixture_li_apply_page.html").as_uri()
RADIO = (HERE / "fixture_redrawn_radio.html").as_uri()

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})

    print("== LinkedIn's apply page with its own Easy Apply button ==")
    page.goto(JOB + "#job")
    sess = Sess()
    started = time.time()
    kind = linkedin.start(page, sess)
    took = time.time() - started
    form_open = page.locator("div[role=dialog] #phone").count() == 1
    check("the second button is pressed without the model", kind == "easy_apply" and form_open
          and any("its own Easy Apply button" in line for line in sess.lines), sess.lines)
    check("  and the form is called open only once it is", sess.lines[-1].startswith("[linkedin] Easy Apply - the application form is open")
          and form_open, sess.lines[-1])
    check("  quickly", took < 8, f"{took:.1f}s")

    print("\n== an apply page that shows the form straight away ==")
    page.goto(JOB.replace("#job", "") + "#jobdirect")
    page.reload()
    sess = Sess()
    started = time.time()
    kind = linkedin.start(page, sess)
    took = time.time() - started
    check("nothing extra is pressed", kind == "easy_apply"
          and not any("its own Easy Apply button" in line for line in sess.lines), sess.lines)
    check("  and no time is spent looking for a second button", took < 4, f"{took:.1f}s")

    print("\n== a radio rebuilt while it is being ticked ==")
    page.goto(RADIO)
    fields = browser.snapshot(page)
    yes = next(f for f in fields if f.get("type") == "radio" and "Yes" in worker._field_label(f))
    started = time.time()
    error = ""
    try:
        worker._set_checked(page, browser.locate(page, yes["id"], str(yes.get("elid") or "")), yes, True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    took = time.time() - started
    # Its replacement - same group, same label - is found and ticked; one
    # with nothing to tie it to the old one is still reported gone (below).
    check("a rebuilt radio's replacement is ticked", not error
          and page.evaluate("() => document.querySelector('#opts input[value=Yes]').checked"), error)
    check("  in seconds, not the sixteen it took", took < 6, f"{took:.1f}s")

    page.goto(RADIO)
    fields = browser.snapshot(page)
    yes = next(f for f in fields if f.get("type") == "radio" and "Yes" in worker._field_label(f))
    page.evaluate("() => document.getElementById('opts').innerHTML = ''")
    stale = False
    try:
        worker._set_checked(page, browser.locate(page, yes["id"], str(yes.get("elid") or "")), yes, True)
    except worker.StaleField:
        stale = True
    except Exception as exc:
        print("    raised", type(exc).__name__, exc)
    check("a radio gone with no replacement is reported gone", stale)
    b.close()

print()
if problems:
    print("LI APPLY PAGE CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("LI APPLY PAGE CHECK PASSED")
