"""iCIMS inside Planview's careers page (Oct 15 2026).

What went wrong on the real page:
  - Phone Country Code is an <a role=combobox>; the scan dropped it as "just
    a link" and read the hidden "Type to Search" box inside its list as the
    field - "it is hidden right now", then "Filled ... = +91" into a box that
    only filters the list;
  - every open and close of that list changed the frame's control count, and
    the page's header search box set the bar a frame must clear, so the scan
    swapped between the form and the page, and the candidate was told to
    click the page's "Submit a Ticket", then "#4", then "Submit a Ticket"...;
  - "#4" was the form's <input type=submit value="Next">.

Part 1 drives the pieces against the replica; part 2 is a whole session.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import e2e_common as e2e  # noqa: E402  (isolation: scratch profile, history, Chrome)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

PAGE = (HERE / "fixture_planview.html").as_uri()
STEP = (HERE / "fixture_icims_phone.html").as_uri()
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


def labels(fields):
    return [worker._field_label(f) for f in fields]


def frame_of(page):
    return page.frame_locator("#icims_content_iframe")


print("== part 1: the pieces ==")
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})

    page.goto(STEP)
    fields = browser.snapshot(page)
    box = next((f for f in fields if f.get("role") == "combobox"), None)
    check("the <a role=combobox> is read as the country-code dropdown",
          box is not None and box.get("tag") == "a" and "Country Code" in worker._field_label(box), labels(fields))
    check("its list's search box is not a field of its own",
          not any(f.get("elid") == "countryCode" for f in fields), labels(fields))
    nxt = next((f for f in fields if (f.get("type") or "") == "submit"), None)
    check("Next is called Next, not '#4'", nxt is not None and worker._field_label(nxt) == "Next",
          nxt and worker._field_label(nxt))
    sess = Sess()
    started = time.time()
    worker._apply_value(page, box, "+91", "", sess, source="profile")
    shown = page.evaluate("() => [document.getElementById('dropdown-select-span').textContent,"
                          " document.getElementById('selectedCountryCode').value]")
    check("+91 is chosen in the list, not typed into its search box", shown == ["(+91) India", "IN"],
          f"{shown} {sess.lines}")
    check("  quickly", time.time() - started < 3, f"{time.time() - started:.2f}s")

    page.goto(STEP + "#preset")
    page.reload()
    fields = browser.snapshot(page)
    box = next((f for f in fields if f.get("role") == "combobox"), None)
    check("a country code already chosen reads as its value", box is not None
          and "(+91) India" in str(box.get("value") or box.get("text") or ""), box)

    # The frame keeps the scan: the list opening and closing, the page's
    # search box coming and going.
    page.goto(PAGE)
    page.wait_for_timeout(500)
    first = labels(browser.snapshot(page))
    check("the scan is the form's frame", "Email" in first and "Submit a Ticket" not in first, first)
    seen = []
    for step in range(6):
        if step % 2 == 0:
            frame_of(page).locator("#dropdown").click()
        else:
            page.keyboard.press("Escape")
        page.evaluate(f"() => toggleSearch({'true' if step % 3 == 0 else 'false'})")
        page.wait_for_timeout(150)
        seen.append("Email" in labels(browser.snapshot(page)))
    check("and keeps it while the list opens and closes and the page's search box comes and goes",
          all(seen), seen)
    b.close()


print("\n== part 2: a whole session ==")
CALLS: list[str] = []


def model(system, user, schema, images=None):
    CALLS.append(user)
    return schema()


model.takes_images = True
worker.make_invoker = lambda cfg, env, purpose: model
sess = e2e.ScriptedSession(["applied"])
sess.t0 = time.time()
job = {"job_id": "test:planview", "company": "Planview", "title": "Sr. Software Engineer",
       "description": "Build things.", "apply_url": PAGE}
worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                   resume_options=lambda: e2e.RESUME_OPTIONS, out_dir=e2e.SCRATCH)
logs = "\n".join(sess.logs)
asked = "\n".join(sess.asked)
check("the session ends applied", sess.status == "applied", sess.status)
check("the country code is chosen from the profile", "Selected '(+91) India'" in logs, logs[-900:])
check("no 'Filled' into the list's search box", "Filled Phone Country Code" not in logs, logs[-900:])
check("the national number box gets the number without +91 (autocomplete=tel-national)",
      "Filled Number = 00000 00000" in logs, logs[-900:])
check("the candidate is pointed at Next, a submit-type button, once",
      "'Next'" in asked and "Submit a Ticket" not in asked and "#4" not in asked and len(sess.asked) == 1,
      sess.asked)
check("no model call was needed", CALLS == [], len(CALLS))

print("\n-- from the job's page, framed --")
CALLS.clear()
sess = e2e.ScriptedSession(["applied"])
sess.t0 = time.time()
job = dict(job, job_id="test:planview-job", apply_url=PAGE + "#job")
worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                   resume_options=lambda: e2e.RESUME_OPTIONS, out_dir=e2e.SCRATCH)
logs = "\n".join(sess.logs)
check("iCIMS's 'Apply for this job online', in the frame, is followed without the model",
      "'Apply for this job online' is a link to the application form - opened it" in logs, logs[-900:])
check("  in its frame: the employer's page stays around it", "Selected '(+91) India'" in logs
      and "Filled Email" in logs, logs[-600:])
check("  and no model call about the job page", CALLS == [], len(CALLS))
check("  the candidate is pointed at Next", len(sess.asked) == 1 and "'Next'" in sess.asked[0], sess.asked)

print()
if problems:
    print("PLANVIEW CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("PLANVIEW CHECK PASSED")
