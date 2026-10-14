"""A text box that the page re-draws when focus leaves it, as Workday does:
the node is replaced by a new one under the same id, holding the value.

On Walmart's Workday form (Oct 2026) every text box took 2.0 s to fill and
every textarea none: the read-back after Tab was still aimed at the old,
discarded node and waited out its two-second timeout before giving it the
benefit of the doubt.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")
history.DB_PATH = Path(TMP.name) / "history.db"

PAGE = """<!DOCTYPE html><html><body>
<label for="addressSection--addressLine1">Address Line 1</label>
<span><input id="addressSection--addressLine1" type="text"></span>
<label for="notes">Notes</label><textarea id="notes"></textarea>
<script>
  // On blur, swap the input for a fresh node with the same id and value.
  function wire(input) {
    input.addEventListener('blur', () => {
      queueMicrotask(() => {
        const fresh = document.createElement('input');
        fresh.type = 'text'; fresh.id = input.id; fresh.value = input.value;
        input.replaceWith(fresh); wire(fresh);
      });
    });
  }
  wire(document.getElementById('addressSection--addressLine1'));
</script></body></html>"""

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines: list[str] = []
        self.refused: dict = {}

    def log(self, line):
        self.lines.append(line)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.set_content(PAGE)
    fields = browser.snapshot(page)
    box = next(f for f in fields if f.get("label") == "Address Line 1")
    sess = Sess()
    started = time.monotonic()
    worker._apply_value(page, box, "12 Example Street", "", sess, source="profile")
    took = time.monotonic() - started
    held = page.locator("#addressSection--addressLine1").input_value()
    check("the re-drawn box holds the value", held == "12 Example Street", held)
    check("and the fill takes well under the 2 s read-back timeout", took < 1.0, f"{took:.2f}s")
    check("it is reported as filled", sess.lines == ["[profile] Filled Address Line 1 = 12 Example Street"],
          str(sess.lines))
    b.close()
TMP.cleanup()

print()
if problems:
    print("REDRAW ON BLUR CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("REDRAW ON BLUR CHECK PASSED")
