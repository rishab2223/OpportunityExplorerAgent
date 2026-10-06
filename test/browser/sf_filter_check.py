"""A dropdown that filters only on keys, through an empty moment.

Ericsson on SuccessFactors (Oct 2026): State/Province is one worldwide list
in 200-row pages. fill() re-drew a page of it without filtering; typed keys
emptied it, and the matches arrived a beat later. The agent took the re-draw
for a filter, then the empty moment for "no hits", and refused Haryana from
"Aarhus, Abaco, Abidjan..." - and left the menu open over the next box.
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


PAGE = """<!doctype html><html><body>
<label for="st">State/Province</label>
<input id="st" role="combobox" aria-owns="st-list" autocomplete="off">
<ul id="st-list" role="listbox" hidden></ul>
<label for="next">Next box</label><input id="next">
<script>
  const WORLD = ['No Selection', 'Aarhus', 'Abaco', 'Abidjan', 'Abu Dhabi'];
  for (let i = 0; i < 40; i++) WORLD.push('Place ' + String(i).padStart(2, '0'));
  WORLD.push('Haryana', 'Tanintharyi', 'Zug');
  const box = document.getElementById('st'), list = document.getElementById('st-list');
  let page = 0;
  function show(rows) {
    list.innerHTML = rows.map(r => '<li role="option">' + r + '</li>').join('');
    list.hidden = false;
    for (const li of list.children) li.onclick = () => { box.value = li.textContent; list.hidden = true; };
  }
  box.addEventListener('click', () => show(WORLD.slice(0, 20)));
  // fill(): a re-draw of a page of the world, no filter.
  box.addEventListener('input', (e) => { if (!e.isTrusted || e.inputType !== 'insertText') {
    page = (page + 1) % 2; show(WORLD.slice(page * 20, page * 20 + 20)); } });
  // keys: empty while it searches, then the matches.
  let timer = null;
  box.addEventListener('keyup', () => {
    show([]); clearTimeout(timer);
    timer = setTimeout(() => show(WORLD.filter(w => w.toLowerCase().includes(box.value.toLowerCase()))), 500);
  });
</script>
</body></html>"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(PAGE)
    fields = {str(f.get("label")): f for f in browser.snapshot(page)}
    st = fields["State/Province"]
    loc = browser.locate(page, st["id"], st.get("elid") or "")
    sess = Sess()
    try:
        worker._commit_combobox(page, loc, "Haryana", "State/Province", "[profile] ", sess, [])
    except Exception as exc:
        sess.log(f"refused: {exc}")
    check("Haryana is picked from the filtered rows", page.input_value("#st") == "Haryana",
          page.input_value("#st"))

    print("\n== a refusal closes the list ==")
    page.set_content(PAGE)
    st = {str(f.get("label")): f for f in browser.snapshot(page)}["State/Province"]
    loc = browser.locate(page, st["id"], st.get("elid") or "")
    try:
        worker._commit_combobox(page, loc, "Atlantis", "State/Province", "", Sess(), [])
        said = "took something"
    except Exception as exc:
        said = str(exc)
    check("Atlantis is refused", "matches none" in said or "took nothing" in said, said)
    check("and the list is closed, not lying over the next box", page.locator("#st-list").is_hidden())
    b.close()

print()
if problems:
    print("SF FILTER CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("SF FILTER CHECK PASSED")
