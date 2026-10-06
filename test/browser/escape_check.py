"""The agent never presses Escape inside an application popup.

EPAM (Oct 2026): the popup closed under the candidate in the middle of step
5. The agent pressed Escape to close a date picker and a suggestion list it
thought it had opened, and EPAM's popup - like LinkedIn's Easy Apply, which
discards the application - closes on Escape.
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


class Sess:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, line):
        self.lines.append(line)
        print("    LOG", line)


# A popup that closes on Escape anywhere, the way EPAM's does. Inside it, a
# date box whose calendar opens on focus and closes on Tab or a click
# elsewhere, and a dropdown button whose list toggles on click.
PAGE = """<!doctype html><html><body>
<p id="outside">Job page</p>
<div id="popup" role="dialog" aria-modal="true" aria-label="Application"
     style="position:fixed; top:10px; left:10px; width:700px; height:500px; background:#fff">
  <label for="startDateAtRecentEmployer">Tenure at Recent employer</label>
  <input id="startDateAtRecentEmployer" value="02/01/2026">
  <button id="pick" aria-haspopup="listbox">Notice Period</button>
  <div id="list" role="listbox" hidden><div role="option">30 days</div><div role="option">60 days</div></div>
</div>
<script>
  window.escapes = 0;
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    window.escapes += 1;
    const p = document.getElementById('popup');
    if (p) p.remove();                       // the application is gone
  });
  const box = document.getElementById('startDateAtRecentEmployer');
  box.addEventListener('focus', () => {
    if (document.querySelector('.react-datepicker')) return;
    const cal = document.createElement('div');
    cal.className = 'react-datepicker'; cal.setAttribute('role', 'dialog');
    cal.setAttribute('aria-label', 'Choose Date'); cal.textContent = 'July 2020';
    box.after(cal);
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') document.querySelectorAll('.react-datepicker').forEach(c => c.remove());
  });
  document.getElementById('pick').addEventListener('click', () => {
    const l = document.getElementById('list'); l.hidden = !l.hidden;
  });
</script>
</body></html>"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(PAGE)
    fields = {str(f.get("label") or f.get("text")): f for f in browser.snapshot(page)}

    print("== typing a date inside the popup ==")
    tenure = fields["Tenure at Recent employer"]
    check("the tenure box is a date box", worker._is_date_box(tenure))
    sess = Sess()
    try:
        worker._type_date_box(page, browser.locate(page, tenure["id"], tenure.get("elid") or ""),
                              "2020-07-01", "Tenure at Recent employer", "[profile] ", sess, tenure)
    except Exception as exc:
        sess.log(f"refused: {exc}")
    check("the popup is still open", page.locator("#popup").count() == 1)
    check("no Escape was pressed", page.evaluate("window.escapes") == 0, str(page.evaluate("window.escapes")))
    check("the date went in, in the box's own order", page.input_value("#startDateAtRecentEmployer") == "07/01/2020",
          page.input_value("#startDateAtRecentEmployer"))
    check("and the calendar is closed", page.locator(".react-datepicker").count() == 0)

    print("\n== a dropdown with no matching option inside the popup ==")
    pick = fields["Notice Period"]
    try:
        worker._pick_listbox(page, browser.locate(page, pick["id"], pick.get("elid") or ""),
                             "Immediate Joiner", "Notice Period", "", Sess())
        said = "took something"
    except Exception as exc:
        said = str(exc)
    check("it is refused, naming the options", "30 days" in said, said)
    check("the popup is still open", page.locator("#popup").count() == 1)
    check("no Escape was pressed", page.evaluate("window.escapes") == 0)
    check("and its list is closed again", page.locator("#list").is_hidden())

    print("\n== outside any popup, Escape is still how a list is closed ==")
    page.set_content("<input id='q'><script>window.escapes = 0; document.addEventListener("
                     "'keydown', e => { if (e.key === 'Escape') window.escapes += 1; });</script>")
    worker._dismiss(page, browser.target(page).locator("#q"))
    check("Escape pressed", page.evaluate("window.escapes") == 1)
    b.close()

print()
if problems:
    print("ESCAPE CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("ESCAPE CHECK PASSED")
