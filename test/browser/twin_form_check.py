"""A form rendered twice under the same ids (talent.recrew.ai, Oct 2026).

The page carries a hidden phone layout above the visible one. The snapshot
read the visible boxes, but locating each by its id in page order found the
hidden twin first, so every fill failed with "it is hidden right now" and
the session ended on five failures in a row.

Scratch profile and temp DB; nothing real is read or written.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "email": "test@example.invalid", "current_ctc": "25 LPA",
}), encoding="utf-8")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines = []
        self.refused = {}

    def log(self, line):
        self.lines.append(line)


VISIBLE = "() => Array.from(document.querySelectorAll('#{0}')).map(e => e.value)"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 900})
    page.goto((HERE / "fixture_twin_form.html").as_uri())
    page.wait_for_timeout(200)

    print("== the snapshot reads the visible copy only ==")
    fields = browser.snapshot(page)
    named = {str(f.get("label") or "").strip(): f for f in fields}
    check("one Name box", sum(1 for f in fields if f.get("elid") == "name_field") == 1)

    print("\n== and the agent writes into that copy, not its hidden twin ==")
    for label in ("Name *", "Email *"):
        box = named[label]
        loc = browser.locate(page, box["id"], str(box.get("elid") or ""))
        check(f"'{label}' is located on screen", loc.is_visible())

    sess = Sess()
    filled = worker._sweep(page, fields, set(), {}, {}, "", sess)
    names = page.evaluate(VISIBLE.format("name_field"))
    ctc = page.evaluate(VISIBLE.format("current_ctc_field"))
    check("the profile filled the visible boxes", filled >= 3, f"{filled} filled; {sess.lines}")
    check("the name went into the visible box", names == ["", "Test User"], str(names))
    check("the CTC went in as lakhs", ctc == ["", "25"], str(ctc))
    check("no box was reported hidden",
          not any("hidden right now" in line for line in sess.lines), str(sess.lines))

    print("\n== a radio of the visible copy ticks ==")
    yes = next(f for f in fields if f.get("elid") == "notice_yes")
    worker._set_checked(page, browser.locate(page, yes["id"], "notice_yes"), yes, True)
    ticked = page.evaluate("() => Array.from(document.querySelectorAll('#notice_yes')).map(e => e.checked)")
    check("the visible Yes is ticked, the hidden one not", ticked == [False, True], str(ticked))

    print("\n== a box the plan filled is not reported as one to fill by hand ==")
    ctc_box = named["Current CTC in LPA(lakhs per annum) *"]
    check("labels are one line", "\n" not in ctc_box["label"], repr(ctc_box["label"]))
    check("a filled box reads as answered now", worker._now_answered(page, ctc_box))
    no_box = next(f for f in fields if f.get("elid") == "notice_no")
    check("a radio whose group has a pick reads as answered", worker._now_answered(page, no_box))
    page.evaluate("() => { const e = document.querySelectorAll('#email_field')[1]; e.value = ''; e.disabled = true; }")
    check("a disabled box is not the candidate's to fill",
          worker._now_answered(page, named["Email *"]))
    check("an option is named with its question",
          worker._question_of(no_box) == "Are you currently on notice period? * (No)", worker._question_of(no_box))

    print("\n== when the marker is gone, the id still finds the one on screen ==")
    page.evaluate("() => document.querySelectorAll('[data-oea-id]').forEach(e => e.removeAttribute('data-oea-id'))")
    box = named["Email *"]
    loc = browser.locate(page, box["id"], "email_field")
    check("located by id, visible copy", loc.is_visible())

    b.close()
TMP.cleanup()

print()
if problems:
    print("TWIN FORM CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("TWIN FORM CHECK PASSED")
