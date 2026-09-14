"""Radix/shadcn controls: a styled button plus a hidden native mirror.

The SourcingXPress failure: dropdowns were read as two fields each (the
button with no options, and a phantom labelled by its own option text),
fill() was attempted on the button, and the answers the candidate typed in
chat never reached the form.

Scratch profile, temp DB.
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
from src.apply import browser, profile, resolver, worker  # noqa: E402

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
    stamp = "radix"

    def log(self, text):
        print(f"    LOG {text}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1100, "height": 900})
    page.goto((HERE / "fixture_radix.html").as_uri())
    page.wait_for_timeout(200)
    sess = Sess()

    fields = browser.snapshot(page)
    print(f"{len(fields)} fields:")
    for f in fields:
        print(f"  id={f['id']:>2} {f['tag']:7} type={str(f.get('type') or ''):9} "
              f"listbox={resolver.is_listbox_button(f)!r:6} label={(f.get('label') or '')[:38]!r}")

    print("\nthe hidden mirrors are gone")
    labels = [str(f.get("label") or "") for f in fields]
    check("no phantom labelled by its options", any("15 Days" in l for l in labels), False)
    check("one control per question", len(fields), 6)

    print("\na combobox button is a dropdown, not a text box")
    notice = next(f for f in fields if f.get("label") == "Select" and f["id"] == 2)
    check("notice period is a listbox button", resolver.is_listbox_button(notice), True)
    worker._pick_listbox(page, browser.locate(page, notice["id"], ""), "Immediate",
                         "Notice Period", "", sess)
    check("notice period chosen", page.locator("#noticeSel").input_value(), "Immediate")

    print("\nthe work type dropdown too")
    work = next(f for f in fields if f["id"] == 1)
    worker._pick_listbox(page, browser.locate(page, work["id"], ""), "On-site",
                         "Current Work Type", "", sess)
    check("work type chosen", page.locator("#workTypeSel").input_value(), "On-site")

    print("\nthe radio the candidate answered yes to")
    fields = browser.snapshot(page)
    yes = next(f for f in fields if f.get("label") == "Yes")
    check("read as a radio", yes.get("type"), "radio")
    check("read as unticked", yes.get("checked"), False)
    worker._set_checked(page, browser.locate(page, yes["id"], ""), yes, True)
    check("relocate = yes", page.locator("#relocYesIn").is_checked(), True)

    print("\nthe consent checkbox")
    fields = browser.snapshot(page)
    consent = next(f for f in fields if "consent" in str(f.get("label") or "").lower())
    check("read as a checkbox", consent.get("type"), "checkbox")
    worker._set_checked(page, browser.locate(page, consent["id"], ""), consent, True)
    check("consent ticked", page.locator("#consentIn").is_checked(), True)

    print("\nticking something already ticked does not untick it")
    fields = browser.snapshot(page)
    consent = next(f for f in fields if "consent" in str(f.get("label") or "").lower())
    check("already ticked is seen", consent.get("checked"), True)
    worker._set_checked(page, browser.locate(page, consent["id"], ""), consent, True)
    check("still ticked", page.locator("#consentIn").is_checked(), True)

    b.close()
TMP.cleanup()

print()
if FAILED:
    print("RADIX CHECK FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("RADIX CHECK PASSED")
