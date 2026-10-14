"""SuccessFactors' repeating sections (Cargill, Oct 2026).

Each section title is an <h2> holding a single collapse toggle, which the
section finder skipped as "a wrapper", so every box sat in the page title's
section. Each Add button reads "\\ue1f6 Add" (an SAP icon-font glyph first),
which the Add matcher did not recognise. Together: Work Experience,
Education and Languages were never opened from the profile, and the
candidate was asked "Should I click ' Add' under 'Education (Optional)'?".

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
from src.apply import browser, profile, resolver, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "languages": "English - Intermediate; Hindi - Fluent",
    "education": "Example University - Bachelors, Computer Science, 2015-2019",
    "jobs": [
        {"title": "Senior Engineer", "company": "Northwind", "start": "07/2020", "end": "01/2026"},
        {"title": "Engineer", "company": "Northwind", "start": "06/2019", "end": "07/2020"},
    ],
}), encoding="utf-8")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 1400})
    page.goto((HERE / "fixture_sf_sections.html").as_uri())
    page.wait_for_timeout(200)

    print("== the Add buttons are read as Adds, each under its own section ==")
    fields = browser.snapshot(page)
    adds = [f for f in fields if worker._is_section_add(f)]
    check("four Add buttons recognised", len(adds) == 4, str([f.get("label") for f in fields][:12]))
    check("no icon glyph left in a label",
          not any(any("" <= c <= "" for c in str(f.get("label") or "")) for f in fields))
    lang_box = next(f for f in fields if f.get("label") == "Preferred Language")
    check("a box after a toggle heading is in that section",
          lang_box.get("section") == "Job-Specific Information", repr(lang_box.get("section")))

    print("\n== so the profile opens one entry per job, school and language ==")
    sess = Sess()
    handled: set[str] = set()
    for _ in range(12):
        if not worker._open_profile_sections(page, browser.snapshot(page), handled, sess):
            break
    opened = page.locator("#log").inner_text()
    check("two work entries", opened.count("work") == 2, opened)
    check("one education entry", opened.count("edu") == 1, opened)
    check("two language entries", opened.count("lang") == 2, opened)
    check("no certification entry (the profile has none)", "cert" not in opened, opened)

    print("\n== an empty section is empty, whatever the Add button carries ==")
    # With ONE job in the profile the Add button itself counted as the entry,
    # and Work Experience was never opened.
    one_job = json.loads(profile.PROFILE_PATH.read_text(encoding="utf-8"))
    profile.PROFILE_PATH.write_text(json.dumps({**one_job, "jobs": one_job["jobs"][:1]}), encoding="utf-8")
    page.goto((HERE / "fixture_sf_sections.html").as_uri())
    page.wait_for_timeout(200)
    handled = set()
    for _ in range(12):
        if not worker._open_profile_sections(page, browser.snapshot(page), handled, Sess()):
            break
    check("one job opens one work entry", page.locator("#log").inner_text().count("work") == 1,
          page.locator("#log").inner_text())
    profile.PROFILE_PATH.write_text(json.dumps(one_job), encoding="utf-8")
    page.goto((HERE / "fixture_sf_sections.html").as_uri())
    page.wait_for_timeout(200)
    handled = set()
    for _ in range(12):
        if not worker._open_profile_sections(page, browser.snapshot(page), handled, Sess()):
            break

    print("\n== and the new boxes belong to their sections ==")
    fields = browser.snapshot(page)
    school = next(f for f in fields if f.get("label") == "School")
    check("School sits under Education", school.get("section") == "Education (Optional)",
          repr(school.get("section")))
    worker._annotate(fields)
    check("the school comes from the profile",
          resolver.resolve(school) == ("Example University", "profile"), repr(resolver.resolve(school)))
    title = next(f for f in fields if f.get("label") == "Job Title")
    check("the first job title comes from the profile",
          resolver.resolve(title) == ("Senior Engineer", "profile"), repr(resolver.resolve(title)))

    print("\n== Preferred Language is the profile's first language ==")
    check("English", resolver.resolve(lang_box) == ("English", "profile"), repr(resolver.resolve(lang_box)))

    b.close()
TMP.cleanup()

print()
if problems:
    print("SF SECTIONS CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("SF SECTIONS CHECK PASSED")
