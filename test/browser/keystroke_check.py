"""A dropdown whose filter only hears real keystrokes.

Rippling's phone country box, from two sessions of evidence: whatever whole
value was typed at it, the SAME seven rows came back - the window around the
currently selected country (+1 US), never a filtered list. Both '+91 IN -
India' and '+91 IN' produced 'TZ, UA, UG, US, UY, UZ, VA'.

A controlled React input behaves exactly like that when its filter runs off
key events: fill() sets the value and fires one input event, which the
component accepts without ever running the filter. worker._retype already
exists for this failure mode, with that reason written on it.

So: typing must fall back to real keystrokes when a fill leaves the list
where it was. The value still has to be matched in full against the result -
narrowing must never be allowed to choose - which is what keeps 'India' from
committing British Indian Ocean Territory (+246), the way a phone code went
wrong once before.

Nothing real is read or written.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print(f"    LOG {text}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 900, "height": 900})
    page.goto((HERE / "fixture_keystroke_combo.html").as_uri())
    page.wait_for_timeout(200)

    fields = browser.snapshot(page)
    combo = next(f for f in fields if f.get("role") == "combobox")
    locator = browser.locate(page, combo["id"], str(combo.get("elid") or ""))

    print("the widget ignores a value set from outside")
    page.click("#search")
    page.wait_for_timeout(150)
    locator.fill("+91 IN - India")
    page.wait_for_timeout(200)
    rows = page.locator("#list .opt")
    seen = [rows.nth(n).inner_text() for n in range(rows.count())]
    check("a fill leaves the list unfiltered", len(seen) > 1 and seen[0].startswith("+247 AC"),
          str(seen[:3]))
    check("and India is nowhere in it",
          not any(s.endswith(" - India") for s in seen),
          str([s for s in seen if "India" in s]))

    print("\nso the real value is committed anyway")
    sess = Sess()
    page.reload()
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    combo = next(f for f in fields if f.get("role") == "combobox")
    locator = browser.locate(page, combo["id"], str(combo.get("elid") or ""))
    worker._commit_combobox(page, locator, "+91 IN - India", "Phone number", "", sess)
    got = page.locator("#search").input_value()
    check("the right country was chosen", got == "+91 IN - India", repr(got))
    check("and it said it had to search for it",
          any("searched" in s for s in sess.logs), str(sess.logs))

    print("\nand a value sharing the fragment still lands on its own row")
    sess2 = Sess()
    page.reload()
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    combo = next(f for f in fields if f.get("role") == "combobox")
    locator = browser.locate(page, combo["id"], str(combo.get("elid") or ""))
    worker._commit_combobox(page, locator, "+246 IO - British Indian Ocean Territory",
                            "Phone number", "", sess2)
    got2 = page.locator("#search").input_value()
    check("not India, which the fragment also matches",
          got2 == "+246 IO - British Indian Ocean Territory", repr(got2))

    print("\nand something that is in no list is still refused")
    sess3 = Sess()
    page.reload()
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    combo = next(f for f in fields if f.get("role") == "combobox")
    locator = browser.locate(page, combo["id"], str(combo.get("elid") or ""))
    refused = False
    try:
        worker._commit_combobox(page, locator, "+999 XX - Atlantis", "Phone number", "", sess3)
    except ValueError:
        refused = True
    check("it refuses rather than committing something", refused)
    check("and the box is left exactly as it was found",
          page.locator("#search").input_value() == "+1 US",
          repr(page.locator("#search").input_value()))

    b.close()

print()
if failures:
    print("KEYSTROKE CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("KEYSTROKE CHECK PASSED")
