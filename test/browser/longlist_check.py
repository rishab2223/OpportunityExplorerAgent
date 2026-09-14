"""A 250-entry country-code combobox, the shape that beat the agent on
Rippling.

The options read "+91 IN - India". The widget searches on the country NAME,
so typing the whole label matched nothing and it went on showing the entire
list alphabetically - "+247 AC - Ascension Island, +376 AD - Andorra,
+971 AE - United Arab Emirates..." - and India was never among the rows it
had rendered. The agent refused, correctly, and asked the candidate to pick.

Narrowing the list with a word out of the value fixes it. What must NOT
change is which option is then chosen: the fragment narrows, and the FULL
value is matched against what comes back. Typing "India" and taking the first
row is exactly how a phone code once became +246, British Indian Ocean
Territory.
"""
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

from src.apply import browser, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()

# Ordered by country code, as the real one is. The padding sits BETWEEN the
# A-codes and the I-codes, so India is row 249: far outside the window the
# widget renders, which is the whole problem.
COUNTRIES = (
    [("AC", "247", "Ascension Island"), ("AD", "376", "Andorra"),
     ("AE", "971", "United Arab Emirates"), ("AF", "93", "Afghanistan"),
     ("AG", "1", "Antigua & Barbuda"), ("AI", "1", "Anguilla"),
     ("AL", "355", "Albania"), ("AM", "374", "Armenia")]
    + [(f"B{n:03d}", str(600 + n), f"Zedland {n}") for n in range(240)]
    + [("ID", "62", "Indonesia"),
       ("IN", "91", "India"),
       ("IO", "246", "British Indian Ocean Territory")]
)

OPTIONS = ",".join(
    f'{{code:"{c}",dial:"{d}",name:"{n}"}}' for c, d, n in COUNTRIES
)

FIXTURE = WORK / "fixture_longlist.html"
FIXTURE.write_text("""<!doctype html><html><body>
<label for="cc">Country code</label>
<input id="cc" role="combobox" aria-haspopup="listbox" aria-controls="list"
       aria-autocomplete="list" autocomplete="off">
<div id="list" role="listbox"></div>
<div id="chosen"></div>
<script>
  const ALL = [""" + OPTIONS + """];
  const list = document.getElementById('list');
  const box = document.getElementById('cc');
  const labelOf = o => '+' + o.dial + ' ' + o.code + ' - ' + o.name;
  function render(rows) {
    list.innerHTML = '';
    // A real widget renders a window, not 250 rows.
    rows.slice(0, 40).forEach(o => {
      const row = document.createElement('div');
      row.setAttribute('role', 'option');
      row.textContent = labelOf(o);
      row.onclick = () => {
        box.value = labelOf(o);
        document.getElementById('chosen').textContent = labelOf(o);
        list.innerHTML = '';
      };
      list.appendChild(row);
    });
  }
  function filter() {
    const q = box.value.trim().toLowerCase();
    // Searches the NAME only: the composed label is not what it matches on,
    // so typing "+91 IN - India" finds nothing and everything is shown.
    const hit = q ? ALL.filter(o => o.name.toLowerCase().includes(q)) : ALL;
    render(hit.length ? hit : ALL);
  }
  box.addEventListener('input', filter);
  box.addEventListener('click', filter);
  filter();
</script></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)

    print("== the whole label finds nothing, so the list stays alphabetical ==")
    page.fill("#cc", "+91 IN - India")
    page.wait_for_timeout(150)
    rows = page.locator("#list [role=option]")
    first = [rows.nth(i).inner_text() for i in range(min(3, rows.count()))]
    check("the widget shows the unfiltered list", "Ascension Island" in first[0], str(first))
    check("and India is not in the rendered window",
          not any("India" in rows.nth(i).inner_text() for i in range(rows.count())))

    print("\n== the agent picks it anyway ==")
    page.fill("#cc", "")
    sess = Sess()
    worker._commit_combobox(page, page.locator("#cc"), "+91 IN - India",
                            "Country code", "", sess)
    check("the right country was committed",
          page.locator("#chosen").inner_text() == "+91 IN - India",
          repr(page.locator("#chosen").inner_text()))
    check("and it said why it had to search",
          any("did not filter" in s for s in sess.logs))

    print("\n== the fragment narrows, it never chooses ==")
    # "Indonesia" also contains no trap, but British Indian Ocean Territory
    # does: searching "India" returns it too, and start-matching must reject it.
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(150)
    sess2 = Sess()
    worker._commit_combobox(page, page.locator("#cc"), "+246 IO - British Indian Ocean Territory",
                            "Country code", "", sess2)
    check("a value sharing the fragment still lands on its own row",
          page.locator("#chosen").inner_text() == "+246 IO - British Indian Ocean Territory",
          repr(page.locator("#chosen").inner_text()))

    print("\n== a value that is in no list is still refused ==")
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(150)
    try:
        worker._commit_combobox(page, page.locator("#cc"), "+999 XX - Atlantis",
                                "Country code", "", Sess())
        check("it refuses rather than committing something", False, "no error raised")
    except ValueError as exc:
        check("it refuses rather than committing something", True)
        check("and nothing was chosen", page.locator("#chosen").inner_text() == "",
              repr(page.locator("#chosen").inner_text()))
        print("      ", str(exc)[:110])
    b.close()

print("\nFAILURES:" if failures else "\nLONG LIST CHECK PASSED")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
