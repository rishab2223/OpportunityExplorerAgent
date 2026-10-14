"""A SuccessFactors application form, filled from the profile end to end
(Cargill, Oct 2026). Every check here failed on the real form:

  - work entries start with their dates and sit too deep for the heading
    walk: dates went to the wrong entry and one job was written twice;
  - Add was clicked again before its entry had been drawn;
  - the UI5 date box threw away "07/2020" (it wants MM/DD/YYYY) and kept a
    typed date only once focus moved on;
  - "Company Location" got the company, "Year of Degree" got the degree;
  - "No" was refused from "No Selection, No, Yes" because typing hid the list;
  - "Computer Science" and "Fluent" were not on their lists, and the nearest
    options ("Computer", "Advanced") were not taken;
  - a model call was spent deciding to click "Expand all sections";
  - Country took "British Indian Ocean Territory" off the first page of its
    list, for India.

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

from src import answers, history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "location": "Gurgaon, India",
    "languages": "English - Intermediate; Hindi - Fluent",
    "education": "Example University - Bachelors, Computer Science, 2015-2019",
    "jobs": [
        {"title": "Senior Engineer", "company": "Northwind", "location": "Noida",
         "start": "07/2020", "end": "01/2026"},
        {"title": "Engineer Intern", "company": "Northwind", "location": "Pune",
         "start": "06/2019", "end": "07/2020"},
    ],
}), encoding="utf-8")
answers.remember("Have you previously worked for Cargill?", "No")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines: list[str] = []
        self.refused: dict = {}

    def log(self, line):
        self.lines.append(line)


DEEP_VALUES = """() => {
  const out = [];
  const walk = (root) => root.querySelectorAll('*').forEach(el => {
    if (el.tagName === 'INPUT') out.push([el.getAttribute('aria-label') || el.id, el.value]);
    if (el.shadowRoot) walk(el.shadowRoot);
  });
  walk(document);
  return out;
}"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1100, "height": 1600})
    page.goto((HERE / "fixture_sf_form.html").as_uri())
    page.wait_for_timeout(200)

    print("== the sections are opened, one entry per profile item ==")
    sess = Sess()
    handled: set[str] = set()
    for _ in range(12):
        if not worker._open_profile_sections(page, browser.snapshot(page), handled, sess):
            break
    page.wait_for_timeout(1000)
    log = page.locator("#log").inner_text()
    check("Expand all sections is clicked first, by rule", log.startswith("expanded"), log)
    check("two work entries for two jobs", log.count("add:work") == 2, log)
    check("one education entry", log.count("add:edu") == 1, log)
    check("two language entries", log.count("add:lang") == 2, log)

    print("\n== the boxes know their section and their entry ==")
    fields = browser.snapshot(page)
    worker._annotate(fields)
    starts = [f for f in fields if (f.get("label") or "").endswith("Start Date")]
    check("both Start Dates are under External Work Experience",
          [f.get("section") for f in starts] == ["External Work Experience"] * 2,
          str([f.get("section") for f in starts]))
    check("and in entries 0 and 1", [f.get("work_entry") for f in starts] == [0, 1],
          str([f.get("work_entry") for f in starts]))

    print("\n== the profile fills them ==")
    attempts: dict = {}
    for _ in range(4):
        worker._sweep(page, browser.snapshot(page), handled, attempts, {}, "", sess)
    values = dict()
    for name, value in page.evaluate(DEEP_VALUES):
        values.setdefault(name, []).append(value)
    print("   ", {k: v for k, v in values.items() if any(v)})
    check("job dates in MM/DD/YYYY, each in its own entry",
          values.get("Start Date") == ["07/01/2020", "06/01/2019"], str(values.get("Start Date")))
    check("end dates too", values.get("End Date") == ["01/01/2026", "07/01/2020"], str(values.get("End Date")))
    titles = [v for k, v in page.evaluate(DEEP_VALUES) if k and k.startswith("t")]
    check("each job's own title", "Senior Engineer" in titles and "Engineer Intern" in titles, str(titles))
    loc_ids = page.evaluate("() => Array.from(document.querySelectorAll('label')).filter(l => "
                            "l.textContent.includes('Company Location')).map(l => "
                            "document.getElementById(l.htmlFor).value)")
    check("Company Location gets the place", loc_ids == ["Noida", "Pune"], str(loc_ids))
    check("Year of Degree gets the year", values.get("Year of Degree") == ["2019"], str(values.get("Year of Degree")))
    check("Degree: Bachelors (approx 16 yrs)", values.get("Degree") == ["Bachelors (approx 16 yrs)"],
          str(values.get("Degree")))
    check("Major (broad): Computer, the nearest heading for Computer Science",
          values.get("Major/Area of Study (broad)") == ["Computer"], str(values.get("Major/Area of Study (broad)")))
    check("Read: Intermediate, then Advanced for Fluent",
          values.get("Read") == ["Intermediate", "Advanced"], str(values.get("Read")))
    check("No is taken from 'No Selection, No, Yes'",
          values.get("Have you previously worked for Cargill?") == ["No"],
          str(values.get("Have you previously worked for Cargill?")))
    check("Country: India, not British Indian Ocean Territory",
          values.get("Country:") == ["India"], str(values.get("Country:")))
    check("every nearest pick is logged as one",
          sum("[best match]" in line for line in sess.lines) >= 2,
          str([line for line in sess.lines if "best match" in line]))
    check("nothing was typed at a list that loses itself on keys",
          not any("keystrokes" in line and "Cargill" in line for line in sess.lines),
          str([line for line in sess.lines if "keystrokes" in line][:3]))

    b.close()
TMP.cleanup()

print()
if problems:
    print("SF FORM CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("SF FORM CHECK PASSED")
