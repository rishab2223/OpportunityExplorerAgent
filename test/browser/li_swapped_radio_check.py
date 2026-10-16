"""From the Voice of Healthcare Easy Apply session (Oct 2026).

LinkedIn swaps the Yes / No option it has just ticked for a new element. The
tick took, but the element that took it was gone, so:

  - the profile's "willing to relocate: Yes" was logged "Could not fill Yes"
    and recorded as refused, and the model was told "'Yes' refused 'yes'";
  - the model's plan (Yes to joining immediately, Yes to being in Gurgaon)
    stopped at its first tick as "the page changed", and the Gurgaon question
    - required only by the star in its wording - was then retired as one the
    model had passed over. The step was called filled in with it unanswered.

Fixture page only; nothing reaches LinkedIn.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import e2e_common as e2e  # noqa: E402,F401  (isolation: scratch profile, history)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

PAGE = (HERE / "fixture_li_swapped_radio.html").as_uri()
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


def option(fields, question, answer):
    return next(f for f in fields if f.get("type") == "radio"
                and question in (f.get("group") or "") and f.get("label") == answer)


def ticked(page, question):
    return page.evaluate("""q => {
      const set = Array.from(document.querySelectorAll('fieldset')).find(s => s.querySelector('legend').textContent.includes(q));
      const on = Array.from(set.querySelectorAll('input')).find(i => i.checked);
      return on ? on.labels[0].textContent : '';
    }""", question)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 900})
    page.goto(PAGE)
    fields = browser.snapshot(page)

    print("== the star makes a radio question required ==")
    radios = [f for f in fields if f.get("type") == "radio"]
    check("every Yes / No under a starred question is required", len(radios) == 6
          and all(f.get("required") for f in radios), [(f.get("group"), f.get("required")) for f in radios])

    print("\n== a tick that swaps the option for a new element ==")
    joining = option(fields, "join immediately", "Yes")
    error = ""
    try:
        worker._set_checked(page, browser.locate(page, joining["id"], joining.get("elid") or ""), joining, True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    check("the swapped tick is a success, not an error", not error, error)
    check("  and the question is answered Yes", ticked(page, "join immediately") == "Yes")

    print("\n== the next tick of the same plan, from the same reading of the page ==")
    gurgaon = option(fields, "based in Gurgaon", "Yes")
    error = ""
    try:
        worker._set_checked(page, browser.locate(page, gurgaon["id"], gurgaon.get("elid") or ""), gurgaon, True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    check("Gurgaon's Yes goes in too", not error and ticked(page, "based in Gurgaon") == "Yes", error)

    print("\n== the profile's answer, through the same path the session used ==")
    relocate = option(fields, "willing to relocate", "Yes")
    sess = Sess()
    error = ""
    try:
        worker._apply_value(page, relocate, "Yes", "", sess, source="profile")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    check("'willing to relocate: Yes' is not 'Could not fill Yes'", not error
          and ticked(page, "willing to relocate") == "Yes", error)
    b.close()

print()
if problems:
    print("LI SWAPPED RADIO CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("LI SWAPPED RADIO CHECK PASSED")
