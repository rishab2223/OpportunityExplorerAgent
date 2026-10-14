"""Oracle Recruiting Cloud, as Adani's form drew it (Oct 2026):

  - the date of birth is three dropdowns, Month / Day / Year, and each got
    the whole "03/10/1997", which none of them could take;
  - the dropdowns open a GRID (aria-haspopup=grid), whose rows were not read
    as options at all: "its list never opened", for Domicile State too;
  - Yes / No questions are pill buttons marked with aria-pressed. Without a
    state they looked like nothing to fill, and "No" typed in the chat for
    two of them was never clicked.

Scratch profile and temp DB; nothing real is read or written.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "date_of_birth": "03/10/1997", "state": "Haryana",
    "location": "Gurgaon, Haryana, India",
    "jobs": [{"title": "Engineer", "company": "Northwind", "start": "07/2020", "end": "01/2026"}],
}), encoding="utf-8")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self, reply=""):
        self.lines: list[str] = []
        self.refused: dict = {}
        self.reply = reply

    def log(self, line):
        self.lines.append(line)

    def ask(self, question, suggestion=""):
        self.lines.append(f"ASK {question}")
        return self.reply


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1100, "height": 1200})
    page.goto((HERE / "fixture_oracle_cx.html").as_uri())

    print("== the pills read as a question with no answer yet ==")
    fields = browser.snapshot(page)
    pills = [f for f in fields if (f.get("group") or "").startswith("Are you related")]
    check("each pill is an option of its question", [(f.get("type"), f.get("label")) for f in pills]
          == [("radio", "Yes"), ("radio", "No")], str([(f.get("type"), f.get("label")) for f in pills]))
    unresolved = {worker._field_label(f) for f in worker._unresolved_fields(fields, set())}
    check("and the unanswered question is something to fill", "Yes" in unresolved or "No" in unresolved,
          str(sorted(unresolved)))

    print("\n== the profile fills what it knows ==")
    sess = Sess()
    handled: set[str] = set()
    attempts: dict = {}
    for _ in range(3):
        worker._sweep(page, browser.snapshot(page), handled, attempts, {"company": "Example Enterprises Limited"},
                      "", sess)
    values = {k: page.locator(f"#{k}").input_value() for k in
              ("month-dateOfBirth-11", "day-dateOfBirth-11", "year-dateOfBirth-11", "domicileState-18")}
    check("Month: October", values["month-dateOfBirth-11"] == "October", str(values))
    check("Day: 3", values["day-dateOfBirth-11"] == "3", str(values))
    check("Year: 1997", values["year-dateOfBirth-11"] == "1997", str(values))
    log = page.locator("#log").inner_text()
    check("'worked with Example before?' is No, from the profile's employers",
          "Have you worked with Example Before ?=No" in log, log)
    check("no box got the whole date", not any("03/10/1997" in line for line in sess.lines),
          str([line for line in sess.lines if "1997" in line]))

    print("\n== a dropdown whose list is a grid ==")
    # Domicile is the state of schooling, not of residence: no profile rule
    # fills it. What failed was reading its open list at all.
    state = next(f for f in browser.snapshot(page) if f.get("label") == "Domicile State")
    sess = Sess()
    worker._apply_value(page, state, "Haryana", "", sess, source="you")
    held = page.locator("#domicileState-18").input_value()
    check("Domicile State: Haryana, picked from the grid's rows", held == "Haryana", f"{held!r} {sess.lines}")

    print("\n== 'No' in the chat clicks the pill ==")
    fields = browser.snapshot(page)
    yes = next(f for f in fields if (f.get("group") or "").startswith("Are you related") and f.get("label") == "Yes")
    action = worker.ApplyAction(action="ask", field_id=yes["id"], value="",
                                question="Are you related to any employee of the Example Group? (Yes/No)",
                                confidence=0.5, reason="not known")
    sess = Sess("No")
    out = worker._run_action(page, action, fields, {"company": "Example"}, "", sess, [], [], set(), {}, {},
                             set(), None, {})
    log = page.locator("#log").inner_text()
    check("the No pill is pressed", "Are you related to any employee of Example Group?=No" in log,
          f"{out}: {sess.lines}")
    fields = browser.snapshot(page)
    pressed = [f.get("label") for f in fields
               if (f.get("group") or "").startswith("Are you related") and f.get("checked")]
    check("and reads back as the answer", pressed == ["No"], str(pressed))

    b.close()
TMP.cleanup()

print()
if problems:
    print("ORACLE CX CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("ORACLE CX CHECK PASSED")
