"""An amount box that formats as you type.

Real failure: "30,00,000" went in as "3,00,00,000" - three crore instead of
thirty lakh - and "35 LPA" was dropped. Scratch profile, temp DB.
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
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}: {got!r}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")


class Sess:
    stamp = "amount"

    def log(self, text):
        print(f"    LOG {text}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 600})
    page.goto((HERE / "fixture_amount.html").as_uri())
    page.wait_for_timeout(150)
    sess = Sess()
    fields = browser.snapshot(page)
    current = next(f for f in fields if "current" in (f.get("label") or "").lower())
    expected = next(f for f in fields if "expected" in (f.get("label") or "").lower())

    print("a value that already carries commas")
    worker._apply_value(page, current, "25,00,000", "", sess, source="profile")
    check("current salary is 25 lakh", page.locator("#current").input_value(), "25,00,000")

    print("\nthe value that became three crore on a real application")
    worker._apply_value(page, expected, "30,00,000", "", sess, source="profile")
    check("expected salary is 30 lakh", page.locator("#expected").input_value(), "30,00,000")

    print("\nan amount written in lakhs, which the box drops")
    page.locator("#expected").fill("")
    worker._apply_value(page, expected, "35 LPA", "", sess, source="estimate")
    check("35 LPA becomes 35 lakh in digits",
          page.locator("#expected").input_value(), "35,00,000")

    b.close()
TMP.cleanup()

print()
if FAILED:
    print("AMOUNT CHECK FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("AMOUNT CHECK PASSED")
