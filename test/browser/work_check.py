"""Does the work-history sweep actually fill a real browser page correctly?

Drives the Workday-shaped replica with a SCRATCH profile and a temp DB - the
user's own profile and job_history.db are never touched.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "email": "test@example.invalid",
    "location": "Bangalore, India",
    "jobs": [
        {"title": "Senior Engineer", "company": "Northwind Systems", "location": "Noida",
         "start": "07/2020", "end": "01/2026", "description": "Backends and pipelines."},
        {"title": "Engineer Intern", "company": "Northwind Systems", "location": "Pune",
         "start": "06/2019", "end": "07/2020", "description": "Rendering work in C++."},
    ],
}), encoding="utf-8")

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}: {got!r}")
    else:
        FAILED.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")


class Sess:
    stamp = "workcheck"

    def log(self, text):
        print(f"    LOG {text}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 1400})
    page.goto((HERE / "fixture_work.html").as_uri())
    page.wait_for_timeout(200)

    fields = browser.snapshot(page)
    written: dict[str, str] = {}
    filled = worker._sweep(page, fields, set(), {}, {"title": "X", "company": "Y"},
                           "", Sess(), written=written)
    print(f"\nboxes filled without a single model call: {filled}\n")

    def val(elid):
        return page.locator(f"#{elid}").input_value()

    print("entry 1 (most recent job)")
    check("job title", val("wd-jt1"), "Senior Engineer")
    check("company", val("wd-co1"), "Northwind Systems")
    check("location", val("wd-loc1"), "Noida")
    check("from month", val("wd-fm1"), "07")
    check("from year", val("wd-fy1"), "2020")
    check("to month", val("wd-tm1"), "01")
    check("to year", val("wd-ty1"), "2026")
    check("description", val("wd-desc1"), "Backends and pipelines.")
    check("currently here (neither job is current)",
          page.locator("#wd-cur1").is_checked(), False)

    print("\nentry 2 (the earlier job, NOT the same values)")
    check("job title", val("wd-jt2"), "Engineer Intern")
    check("company", val("wd-co2"), "Northwind Systems")
    check("location", val("wd-loc2"), "Pune")
    check("from month", val("wd-fm2"), "06")
    check("from year", val("wd-fy2"), "2019")
    check("to month", val("wd-tm2"), "07")
    check("to year", val("wd-ty2"), "2020")
    check("description", val("wd-desc2"), "Rendering work in C++.")

    print("\neducation is not swept up by the work block")
    check("school left alone", val("edu-school"), "")
    check("education location left alone", val("edu-loc"), "")

    print("\nthe delete button belongs to its own entry")
    entry1 = [f for f in fields if str(f.get("section") or "").endswith("1")]
    named = worker._entry_remove_button(fields, entry1)
    check("entry 1 picks its own Delete", (named or {}).get("elid"), "del1")

    b.close()
TMP.cleanup()

print()
if FAILED:
    print("WORK CHECK FAILED")
    for line in FAILED:
        print("  " + line)
    sys.exit(1)
print("WORK CHECK PASSED")
