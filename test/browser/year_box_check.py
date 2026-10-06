"""A year with no month goes into a box that takes a year, and is refused -
never padded with a made-up month - by one that wants a full date.

EPAM (Oct 2026): "Education Years" start and end boxes. The profile knows
2015-2019; the model offered "2015", and the agent refused it outright on the
guess that the box wanted MM/YYYY, then stalled on "still empty: Education
Years".
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


# Two react-datepicker-shaped boxes. Typed text that is not a full date is
# wiped when the picker closes, as react-datepicker does. #years opens a
# twelve-year grid (showYearPicker); #full opens a day grid.
PAGE = """<!doctype html><html><body>
<label for="years">Education Years</label><input id="years" placeholder="Select start date">
<label for="full">Graduation date</label><input id="full" placeholder="Select date">
<script>
  function close() { document.querySelectorAll('.react-datepicker').forEach(c => c.remove()); }
  function grid(input, kind, first) {
    close();
    const cal = document.createElement('div');
    cal.className = 'react-datepicker';
    const prev = document.createElement('button'); prev.className = 'react-datepicker__navigation--previous';
    const next = document.createElement('button'); next.className = 'react-datepicker__navigation--next';
    cal.append(prev, next);
    if (kind === 'year') {
      const head = document.createElement('div'); head.textContent = first + ' - ' + (first + 11);
      cal.append(head);
      for (let y = first; y < first + 12; y++) {
        const c = document.createElement('div'); c.className = 'react-datepicker__year-text'; c.textContent = y;
        c.onclick = () => { input.value = String(y); input.dataset.ok = '1'; close(); };
        cal.append(c);
      }
      prev.onclick = () => grid(input, 'year', first - 12);
      next.onclick = () => grid(input, 'year', first + 12);
    } else {
      const head = document.createElement('div'); head.textContent = 'October 2026'; cal.append(head);
    }
    document.body.append(cal);
  }
  for (const [id, kind] of [['years', 'year'], ['full', 'day']]) {
    const box = document.getElementById(id);
    box.addEventListener('click', () => grid(box, kind, 2025));
    box.addEventListener('input', () => { box.dataset.ok = ''; });
    box.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      close();
      // react-datepicker keeps only what parses as a date in its format.
      const ok = kind === 'year' ? false : /^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(box.value);
      if (!ok && !box.dataset.ok) box.value = '';
    });
  }
</script>
</body></html>"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(PAGE)
    fields = {str(f.get("label")): f for f in browser.snapshot(page)}

    print("== a year-only picker ==")
    years = fields["Education Years"]
    sess = Sess()
    try:
        worker._type_date_box(page, browser.locate(page, years["id"], years.get("elid") or ""),
                              "2015", "Education Years", "[profile] ", sess, years)
        ok = True
    except Exception as exc:
        ok = False
        sess.log(f"refused: {exc}")
    check("2015 goes in", ok and page.input_value("#years") == "2015", page.input_value("#years"))
    check("  chosen in the year grid, ten years back", any("chosen in the date picker" in l for l in sess.lines),
          str(sess.lines))

    print("\n== a box that wants a full date ==")
    full = fields["Graduation date"]
    sess2 = Sess()
    try:
        worker._type_date_box(page, browser.locate(page, full["id"], full.get("elid") or ""),
                              "2019", "Graduation date", "[profile] ", sess2, full)
        said = ""
    except Exception as exc:
        said = str(exc)
    check("it is refused, by name", "profile does not hold" in said or "only the year" in said, said)
    check("  and nothing made up is left in it", page.input_value("#full") == "", page.input_value("#full"))

    print("\n== a calendar open over the application ==")
    # react-datepicker's popup is role=dialog "Choose Date"; read as the page,
    # the step's own boxes vanished and a question about one ended as "gone".
    page.set_content("""<!doctype html><html><body>
      <div role="dialog" aria-modal="true" aria-label="Application"
           style="position:fixed; top:0; left:0; width:800px; height:600px">
        <label for="start">Education Years</label><input id="start">
        <label for="msg">Message to Hiring Team</label><textarea id="msg"></textarea>
        <div class="react-datepicker" role="dialog" aria-label="Choose Date" aria-modal="true"
             style="position:absolute; top:60px; left:0; width:320px; height:300px; background:#fff">
          <button class="react-datepicker__navigation--previous">Previous Month</button>
          <div class="react-datepicker__day">20</div><button>Today</button>
        </div>
      </div></body></html>""")
    labels = [str(f.get("label") or "") for f in browser.snapshot(page)]
    check("the application's boxes are still read", "Message to Hiring Team" in labels
          and "Education Years" in labels, str(labels))
    b.close()

print()
if problems:
    print("YEAR BOX CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("YEAR BOX CHECK PASSED")
