"""A dropdown whose answer matches nothing (CBTS, Oct 2026).

Its list showed nothing until typed at, and "No match found." under a value
it did not offer. The agent reported "its list never opened", and its
pictures for the model showed the typed value over "No match found." - three
times - so nobody ever saw the options. Now: with the box cleared, the whole
list is read; an answer it holds in other words is taken, one it does not is
refused naming the real options; and the picture shows the list, not the
empty filter.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import e2e_common as e2e  # noqa: E402,F401  (isolation: scratch profile, history)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

PAGE = (HERE / "fixture_nomatch_list.html").as_uri()
problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, line):
        self.lines.append(line)
        print("    LOG", line)


def notice_box(page):
    page.goto(PAGE)
    page.reload()
    fields = browser.snapshot(page)
    return next(f for f in fields if f.get("elid") == "notice")


check("'No match found.' is not an option", not worker._real_suggestions(["No match found."])
      and worker._real_suggestions(["Up to 30 days"]))

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 800})

    print("== an answer the list holds in other words ==")
    box = notice_box(page)
    sess = Sess()
    try:
        worker._apply_value(page, box, "Immediate", "", sess, source="profile")
        error = ""
    except Exception as exc:
        error = str(exc)
    chosen = page.evaluate("() => document.body.dataset.chosen || ''")
    check("'Immediate' takes 'Immediately' from the whole list", chosen == "Immediately", f"{chosen!r} {error}")
    check("  and says it read the whole list", any("read the whole list" in line for line in sess.lines)
          or any("unfiltered list" in line for line in sess.lines) or chosen == "Immediately", sess.lines)

    print("\n== an answer the list does not hold ==")
    box = notice_box(page)
    sess = Sess()
    try:
        worker._apply_value(page, box, "Initech Systems", "", sess, source="profile")
        error = ""
    except Exception as exc:
        error = str(exc)
    check("refused, naming the real options - not 'its list never opened'",
          "Up to 30 days" in error and "never opened" not in error, error)
    check("  and nothing was chosen", not page.evaluate("() => document.body.dataset.chosen || ''"))
    check("  and the typed text is not left in the box", page.input_value("#notice") == "")

    print("\n== the picture for the model ==")
    box = notice_box(page)
    rows: list[str] = []
    png = worker._look_at(page, box, open_list=True, typed="Initech Systems", rows_out=rows)
    check("a refused answer's picture shows the list, not 'No match found.'",
          png is not None and "Up to 30 days" in rows and "No match found." not in rows, rows)
    b.close()

print()
if problems:
    print("NOMATCH LIST CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("NOMATCH LIST CHECK PASSED")
