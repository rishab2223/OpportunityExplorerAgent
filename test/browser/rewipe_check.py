"""A form that accepts a programmatic fill and then wipes it.

This is the SourcingXPress failure: three boxes logged as "Filled" that the
candidate then saw empty in the browser. Scratch profile, temp DB.
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
    "phone": "+91 00000 00000", "location": "Bangalore, India",
}), encoding="utf-8")

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}: {got!r}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")


class Sess:
    stamp = "rewipe"

    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)
        print(f"    LOG {text}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1100, "height": 800})
    page.goto((HERE / "fixture_rewipe.html").as_uri())
    page.wait_for_timeout(80)          # fill BEFORE hydration, as happened

    sess = Sess()
    written = {}
    fields = browser.snapshot(page)
    worker._sweep(page, fields, set(), {}, {"title": "X", "company": "Y"},
                  "", sess, written=written)
    print("\n  the sweep believes it filled:", sorted(written.values()))

    # Hydration lands and wipes them, exactly as the real page did.
    page.wait_for_function("() => window.__hydrated === true", timeout=5000)
    print("  after the page re-rendered:")
    for elid in ("firstName", "lastName"):
        print(f"    #{elid} = {page.locator('#' + elid).input_value()!r}")

    print("\n  the repair pass, which runs before the step is declared ready:")
    fields = browser.snapshot(page)
    fixed = worker._repair_written(page, fields, written, sess)
    check("boxes put back", fixed, len([v for v in written.values()]))
    check("first name", page.locator("#firstName").input_value(), "Test")

    print("\n  and it is quiet when nothing was wiped:")
    again = worker._repair_written(page, browser.snapshot(page), written, sess)
    check("no needless retyping", again, 0)

    b.close()
TMP.cleanup()

print()
if FAILED:
    print("REWIPE CHECK FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("REWIPE CHECK PASSED")
