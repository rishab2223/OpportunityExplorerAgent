"""Wait for a form that rebuilds itself from the resume you just gave it.

UKG/UltiPro reads the uploaded resume and fills Work Experience and Education
from the parse, re-rendering both sections. On the USP application the agent
uploaded at 55.7s and clicked "Add Experience" at 56.3s - six tenths of a
second later, into a section the site had not finished building. The panel
that opened never completed: its overlay stayed up, the panel collapsed to
0x0, and the page was unusable for the candidate as much as for the agent.

browser.wait_quiet waits for the shape to stop moving. It is deliberately not
settle(), which waits for the page to CHANGE from a known shape and is right
after an action whose effect we expect: here no change may happen at all, and
a site that does nothing with the resume must not pay the whole cap for it.

Scratch only. Nothing real is read or written.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402

TMP = tempfile.TemporaryDirectory()
RESUME = Path(TMP.name) / "a_candidate_resume.pdf"
RESUME.write_bytes(b"%PDF-1.4\n% scratch\n")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def log_of(page):
    return page.locator("#log").inner_text().strip()


def titles(page):
    return page.eval_on_selector_all(".title", "els => els.map(e => e.value)")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})

    print("waiting only for quiet is not enough")
    # The trap, and the reason the first version of this shipped broken: a
    # parse that has not STARTED is indistinguishable from one that has
    # finished. The real session read 1.1s of perfectly quiet page and clicked
    # straight into the rebuild.
    #
    # ?manual, so the parse cannot begin until this test says so. On a timer
    # it raced wait_quiet's polling - which stretches when the machine is busy
    # - and the check failed inside the sweep for a reason that had nothing to
    # do with the code it is testing.
    page.goto((HERE / "fixture_parse_rebuild.html").as_uri() + "?manual")
    page.wait_for_timeout(150)
    page.set_input_files("#resume", str(RESUME))
    check("nothing has been rebuilt yet", "parsed" not in log_of(page), repr(log_of(page)))
    check("and quiet alone calls that settled",
          browser.wait_quiet(page, timeout=12000) is True)
    check("with the parse still not started",
          "parsed" not in log_of(page), repr(log_of(page)))
    # Which is the whole bug: anything done here is done to a form that is
    # about to be replaced.
    page.click("#add")
    page.evaluate("() => startParse()")
    page.wait_for_timeout(1400)
    check("and the entry made in that gap is thrown away",
          len(titles(page)) == 2 and "" not in titles(page), str(titles(page)))

    print("\nwaiting for it to start, then to finish, does work")
    page.goto((HERE / "fixture_parse_rebuild.html").as_uri())
    page.wait_for_timeout(150)
    page.set_input_files("#resume", str(RESUME))
    after_upload = browser.page_shape(page)
    began = browser.settle(page, after_upload, 12000)
    if began:
        browser.wait_quiet(page, timeout=12000)
    check("the rebuild is seen to start", began is True)
    check("and is waited out to the end", "parsed" in log_of(page), repr(log_of(page)))
    page.click("#add")
    check("the click now lands after the rebuild",
          "add-during-parse" not in log_of(page), repr(log_of(page)))
    check("and the entry survives",
          len(titles(page)) == 3 and titles(page)[-1] == "", str(titles(page)))

    print("\na form that is not rebuilding costs almost nothing")
    # A cap that behaved like a sleep would put this on every upload on every
    # site, which is how a 110s suite becomes a 300s one.
    page.goto((HERE / "fixture_form.html").as_uri())
    page.wait_for_timeout(200)
    started = time.time()
    quiet = browser.wait_quiet(page, timeout=12000)
    waited = time.time() - started
    check("a still page settles at once", quiet is True)
    check("and pays the quiet window, not the cap",
          waited < 12.0, f"{waited:.1f}s of a 12s cap")

    # The cap - "a page that never stops moving is not waited on for ever" -
    # is proved in WaitQuietTests instead. It lived here, as a page rewriting
    # itself on a 100ms timer, and failed twice inside a parallel sweep: a
    # starved Chromium throttles that timer, the DOM stops changing, and the
    # premise stops being true. A real browser under load cannot promise to
    # never settle; a fake page can.

    b.close()
TMP.cleanup()

print()
if failures:
    print("SETTLE CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("SETTLE CHECK PASSED")
