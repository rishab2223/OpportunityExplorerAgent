"""A field re-mounted inside a popup is never found behind the popup.

EPAM (Oct 2026): after Primary Skill was picked, the step re-drew its
dropdowns with fresh react-select ids, and the lookup for Total Professional
Experience - its marker gone, its old id now worn by the job page's location
box behind the application popup - landed on that box. The click timed out
under the overlay, the keys went to the location box, Enter picked "India:
Chennai", and the agent logged "Selected 'India: Chennai' for Total
Professional Experience".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


PAGE = """<!doctype html><html><body>
<h1>Lead Software Developer</h1>
<label for="loc">Location</label><input id="loc" value="India: Chennai">
<div role="dialog" aria-modal="true" aria-label="Application"
     style="position:fixed; top:20px; left:20px; width:700px; height:400px; background:#fff">
  <label for="rs-5-input">Total Professional Experience*</label>
  <input id="rs-5-input" value="7 years">
  <button type="submit">NEXT</button>
</div>
</body></html>"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(PAGE)
    fields = browser.snapshot(page)
    total = next(f for f in fields if str(f.get("label") or "").startswith("Total"))
    check("the step is read inside the popup",
          [str(f.get("label")) for f in fields if f.get("tag") == "input"] == ["Total Professional Experience*"],
          str([f.get("label") for f in fields]))
    check("  found by its marker", browser.locate(page, total["id"], total.get("elid") or "")
          .input_value() == "7 years")

    # The step re-draws: Total's input is a fresh node with a fresh id, and the
    # old id turns up on the location box behind the popup.
    page.evaluate("""() => {
      const old = document.getElementById('rs-5-input');
      const fresh = document.createElement('input');
      fresh.id = 'rs-9-input'; fresh.value = '7 years';
      old.replaceWith(fresh);
      document.querySelector('label[for="rs-5-input"]').setAttribute('for', 'rs-9-input');
      document.getElementById('loc').id = 'rs-5-input';
    }""")
    stale = browser.locate(page, total["id"], total.get("elid") or "")
    check("the stale lookup does not reach behind the popup", stale.count() == 0,
          f"{stale.count()} match(es): " + (stale.input_value() if stale.count() else ""))
    problem, _ = browser.await_usable(stale)
    check("  and reads as gone, so the page is read again", problem == "gone", repr(problem))

    # Read again, it is found where it now is.
    fresh = next(f for f in browser.snapshot(page) if str(f.get("label") or "").startswith("Total"))
    check("a fresh read finds the re-drawn box",
          browser.locate(page, fresh["id"], fresh.get("elid") or "").get_attribute("id") == "rs-9-input")

    # With no popup, the element's own id still finds a re-rendered input
    # (Workday's search box), as before.
    page.set_content('<label for="w-1">City</label><input id="w-1" value="x">')
    city = browser.snapshot(page)[0]
    page.evaluate("() => { const o = document.getElementById('w-1'); const n = document.createElement('input');"
                  " n.id = 'w-1'; n.value = 'y'; o.replaceWith(n); }")
    check("without a popup, the id still finds the replacement",
          browser.locate(page, city["id"], city.get("elid") or "").input_value() == "y")

    print("\n== a dropdown is never reported as answered by something else ==")
    for shown, wanted, same in (("6 years", "6", True), ("Use this ZIP Code: \"560001\"", "560001", True),
                                ("Available now", "Immediate Joiner", True), ("Node.js", "Node.js", True),
                                ("India: Chennai", "6", False), ("16 years", "6", False),
                                ("Amazon Web Services", "Node.js", False)):
        check(f"'{shown}' {'answers' if same else 'does not answer'} '{wanted}'",
              worker._same_answer(shown, wanted) is same)
    b.close()

print()
if problems:
    print("STALE LOCATE CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("STALE LOCATE CHECK PASSED")
