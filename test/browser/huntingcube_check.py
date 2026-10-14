"""Hunting Cube's apply card (huntingcube.ai, Oct 2026).

Every <label> is bare - no `for`, not wrapping - and every box has a
placeholder, so the email box was listed as "you@email.com" and the CTC box
as "e.g. 15": no profile rule could claim either, and the candidate was
asked what a box called "e.g. 15" was. The resume input carries the hidden
attribute inside the upload label and was not seen at all.

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
from src.apply import browser, profile, resolver, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.invalid",
    "notice_period": "Immediate Joiner",
    "current_ctc": "25 LPA",
}), encoding="utf-8")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 900})
    page.goto((HERE / "fixture_huntingcube.html").as_uri())
    page.wait_for_timeout(200)

    print("== the boxes are named by their labels, not their placeholders ==")
    fields = browser.snapshot(page)
    by_label = {str(f.get("label") or ""): f for f in fields}
    print("  labels:", [str(f.get("label") or "")[:40] for f in fields])
    check("the email box is 'Email*'", "Email*" in by_label, str(list(by_label)))
    check("the CTC box is 'Your Current CTC?'", "Your Current CTC?" in by_label)
    check("the notice box keeps its label", "Your Notice Period?" in by_label)
    check("a placeholder with no label near it still names its box",
          "Search jobs" in by_label)

    print("\n== the hidden resume input is seen, through its label ==")
    uploads = [f for f in fields if f.get("type") == "file"]
    check("one file input", len(uploads) == 1, str(len(uploads)))
    if uploads:
        check("it is the resume", worker.is_resume_field(uploads[0]),
              repr(uploads[0].get("label")) + " / " + repr(uploads[0].get("section")))

    print("\n== so the profile answers them ==")
    email = resolver.resolve(by_label.get("Email*", {}))
    check("email from the profile", email == ("test@example.invalid", "profile"), repr(email))
    ctc = resolver.resolve(by_label.get("Your Current CTC?", {}))
    check("current CTC resolves from the profile", ctc is not None and ctc[1] == "profile", repr(ctc))
    notice_box = by_label.get("Your Notice Period?", {})
    check("the notice box already shows Immediately, so it is left alone",
          notice_box.get("value") == "Immediately" and resolver.resolve(notice_box) is None,
          repr(notice_box.get("value")))

    print("\n== and the CTC lands as a number in LPA, inside the box's three characters ==")
    sess = Sess()
    ctc_box = by_label["Your Current CTC?"]
    try:
        worker._apply_value(page, ctc_box, ctc[0], "", sess, source="profile")
    except Exception as exc:
        sess.log(f"refused: {exc}")
    shown = page.evaluate("() => document.querySelector('input[placeholder=\"e.g. 15\"]').value")
    check("the box shows 25", shown == "25", repr(shown) + " " + str(sess.lines))

    b.close()
TMP.cleanup()

print()
if problems:
    print("HUNTING CUBE CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("HUNTING CUBE CHECK PASSED")
