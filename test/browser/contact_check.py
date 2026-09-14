"""The whole contact defence, end to end, on a page that behaves like Esko:
the ATS parses the uploaded resume a moment after the fields are filled and
replaces the e-mail with its own mis-read of it."""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()          # never the real profile or bank
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
EMAIL, PHONE = "test_user@example.invalid", "9000000000"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "email": EMAIL, "phone": PHONE,
    "location": "Bangalore, India",
}), encoding="utf-8")

FIXTURE = WORK / "fixture_contact.html"
FIXTURE.write_text(f"""<!doctype html><html><body>
<h3>My Information</h3>
<label for="e">Email address*</label><input id="e" type="text" name="email">
<label for="p">Phone number*</label><input id="p" type="tel" name="phone">
<label for="x">Phone Extension</label><input id="x" type="text" name="extension">
<label for="m">Manager email</label><input id="m" type="text" name="manageremail">
<button id="next">Review</button>
<script>
  // the ATS parses the uploaded resume and overwrites what was typed, losing
  // the underscore exactly as Esko did
  window.parseResume = () => {{
    const box = document.getElementById('e');
    box.value = box.value.replace('_', '');
    document.getElementById('p').value = '+91 90000 00000';   // reformatted, same number
  }};
</script></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)
    sess = Sess()
    handled: set[str] = set()
    written: dict[str, str] = {}
    attempts: dict[str, int] = {}
    job = {"title": "Engineer", "company": "DummyCo"}

    worker._sweep(page, browser.snapshot(page), handled, attempts, job, "", sess, written=written)
    if page.locator("#e").input_value() != EMAIL:
        failures.append(f"first fill left {page.locator('#e').input_value()!r}")

    # the ATS parse lands, and the next sweep must put the address back
    page.evaluate("() => window.parseResume()")
    mangled = page.locator("#e").input_value()
    print("   after the ATS parsed the resume:", repr(mangled))
    fields = browser.snapshot(page)
    warnings = worker._contact_warnings(fields)
    print("   warnings:", warnings)
    if not any("Email address*" in w for w in warnings):
        failures.append(f"no warning for the mangled address: {warnings}")
    if any("Phone" in w for w in warnings):
        failures.append(f"the reformatted phone was reported as wrong: {warnings}")
    worker._sweep(page, fields, handled, attempts, job, "", sess, written=written)
    back = page.locator("#e").input_value()
    print("   after the corrective sweep:", repr(back))
    if back != EMAIL:
        failures.append(f"the address was not put back: {back!r}")
    if page.locator("#m").input_value():
        failures.append("the manager's e-mail box was filled")
    if not any("putting it back" in line for line in sess.logs):
        failures.append("the correction was not logged")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nCONTACT CHECK PASSED")
