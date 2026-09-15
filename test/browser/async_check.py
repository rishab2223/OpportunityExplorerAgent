"""A dropdown that searches on the server and answers a beat late.

From the myKaarma session of Sep 15 2026, where the instrumentation added the
night before paid for itself in two lines:

    Location: the list did not move on 'Bangalore, India'; typing it as keystrokes
    Location: typing moved nothing either - this widget is not filtering...
    Could not fill Location: ... pick one of: Bengaluru, Karnataka, India, ...

The list HAD moved. Those two Bengaluru rows are the search results for
"Bangalore" - the agent printed them in the very error that said nothing
matched. It had read the list from before it typed, because
_wait_for_options stops as soon as the list holds real suggestions and a
stale list holds real suggestions already.

So the wait has to be for CHANGE, not for existence.

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
    page.goto((HERE / "fixture_async_combo.html").as_uri())
    page.wait_for_timeout(200)

    fields = browser.snapshot(page)
    combo = next(f for f in fields if f.get("role") == "combobox")
    locator = browser.locate(page, combo["id"], str(combo.get("elid") or ""))

    print("the answer has not arrived when the fill returns")
    page.click("#search")
    page.wait_for_timeout(150)
    locator.fill("+91 IN - India")
    page.wait_for_timeout(200)
    rows = page.locator("#list .opt")
    seen = [rows.nth(n).inner_text() for n in range(rows.count())]
    check("the old rows are still showing right after the fill",
          len(seen) > 1 and seen[0].startswith("+247 AC"), str(seen[:3]))
    check("and India has not arrived yet",
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
    print("ASYNC CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("ASYNC CHECK PASSED")
