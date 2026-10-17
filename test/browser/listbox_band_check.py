"""Dropdown BUTTONS (Workday's listbox button): a band for a number, the box
read back after the click, and a comma inside one option.

Code review, Oct 2026: "6" into a "5-7 years" button list failed and cost a
model call; a list that re-drew under the pointer was logged as selected on
the wrong row; and a multi-select split "Gurgaon, India" into two picks.

Scratch profile, temp DB.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
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


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class Sess:
    stamp = "listbox"

    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)
        print(f"    LOG {text}")


def box(page, elid):
    for f in browser.snapshot(page):
        if f.get("elid") == elid or f.get("id_attr") == elid:
            return f
    return next(f for f in browser.snapshot(page) if elid in str(f.get("label") or "").lower())


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 900, "height": 700})
    page.goto((HERE / "fixture_listbox_band.html").as_uri())
    page.wait_for_timeout(200)

    print("a number finds its band in a button list")
    sess = Sess()
    worker._pick_listbox(page, page.locator("#exp"), "6", "Years of experience", "", sess)
    check("'5-7 years' is chosen for 6", page.locator("#exp").inner_text() == "5-7 years",
          page.locator("#exp").inner_text())
    check("and the log says why", any("falls in that range" in line for line in sess.lines), str(sess.lines))

    print("a click the list moved under is not reported as chosen")
    sess = Sess()
    try:
        worker._pick_listbox(page, page.locator("#shift"), "3-5 years", "Total experience", "", sess)
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("refused, naming what the box shows", "but the box shows '5-7 years'" in refused, refused)
    check("never logged as selected", not any(line.startswith("Selected") for line in sess.lines), str(sess.lines))

    print("one option with a comma in it is one pick")
    sess = Sess()
    worker._pick_listbox(page, page.locator("#loc"), "Gurgaon, India", "Preferred locations", "", sess,
                         multi=True)
    log = page.locator("#log").inner_text()
    check("'Gurgaon, India' taken whole, once", log.count("loc=") == 1 and "loc=Gurgaon, India;" in log, log)

    b.close()

print("\nFAILED: " + ", ".join(FAILED) if FAILED else "\nall listbox checks passed")
sys.exit(1 if FAILED else 0)
