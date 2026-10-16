"""TurboHire's dropdowns (Purplle, Oct 2026): Material-UI 4 autocompletes.

role=combobox sits on the wrapper, not the input, so the snapshot read each
one as a plain text box. Nothing opened them or read their options; the model
was sent pictures of the closed boxes, guessed "B.Tech" for a list holding
"Engineering" and "6" for one holding "3-7 Years", and every guess cost a
failed search of the box and another model call.

Now: they are dropdowns; their options are read (once) and put on the field
for the model, and nothing is chosen by reading them; a list that only
searches as you type is left alone; and an option name goes in.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import e2e_common as e2e  # noqa: E402,F401  (isolation: scratch profile, history)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

PAGE = (HERE / "fixture_mui4_autocomplete.html").as_uri()
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


def by_label(fields, words):
    return next(f for f in fields if words in (f.get("label") or ""))


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 900})
    page.goto(PAGE)
    fields = browser.snapshot(page)

    print("== a Material-UI 4 autocomplete is a dropdown ==")
    edu, exp, city = by_label(fields, "Education"), by_label(fields, "Experience"), by_label(fields, "Location")
    ctc = by_label(fields, "Current CTC")
    check("its input reads as a combobox", all(f.get("role") == "combobox" for f in (edu, exp, city)),
          [f.get("role") for f in (edu, exp, city)])
    check("  and a plain number box does not", ctc.get("role") != "combobox", ctc.get("role"))

    print("\n== its options are read for the model before it is asked ==")
    sess = Sess()
    unresolved = worker._unresolved_fields(fields, set())
    started = time.time()
    worker._read_lists(page, unresolved, sess)
    took = time.time() - started
    check("Education's options", edu.get("options") == ["Engineering", "Medical", "Management", "Science",
                                                      "Commerce", "Arts", "Others"], edu.get("options"))
    check("Experience's options", exp.get("options") == ["0-3 Years", "3-7 Years", "7-10 Years", "10+ Years"],
          exp.get("options"))
    check("a list that only searches as you type gets none", not city.get("options"), city.get("options"))
    check("  and reading chose nothing, and left no list open",
          page.evaluate("() => !document.body.dataset.edu && !document.body.dataset.exp"
                        " && !document.querySelector('.popper')"))
    check("  in a few seconds", took < 6, f"{took:.1f}s")
    check("  and says so in the log", any("Read the options of 4 dropdown(s)" in line for line in sess.lines),
          sess.lines)
    prompt_fields = worker._prompt_fields(fields, set())[0]
    check("the options are in what the model is sent",
          any(f.get("options") == exp["options"] for f in prompt_fields))

    print("\n== read once a session ==")
    again = browser.snapshot(page)
    started = time.time()
    worker._read_lists(page, worker._unresolved_fields(again, set()), sess)
    check("the next pass takes them from memory", time.time() - started < 0.5
          and by_label(again, "Experience").get("options") == exp["options"], f"{time.time() - started:.1f}s")

    print("\n== the model's option goes in ==")
    sess = Sess()
    for field, value in ((edu, "Engineering"), (exp, "3-7 Years")):
        error = ""
        try:
            worker._apply_value(page, field, value, "", sess, source="")
        except Exception as exc:
            error = str(exc)
        check(f"'{value}' is chosen", not error, error)
    check("  and the page holds both", page.evaluate("() => [document.body.dataset.edu, document.body.dataset.exp]")
          == ["Engineering", "3-7 Years"])

    print("\n== a number into a list of ranges ==")
    page.goto(PAGE)
    page.reload()
    fields = browser.snapshot(page)
    sess = Sess()
    error = ""
    try:
        worker._apply_value(page, by_label(fields, "Experience"), "6", "", sess, source="profile")
    except Exception as exc:
        error = str(exc)
    check("6 goes into '3-7 Years', with no model and nothing typed",
          not error and page.evaluate("() => document.body.dataset.exp") == "3-7 Years", error or sess.lines)

    print("\n== a place, in a list searched as you type (Purplle, Oct 2026) ==")
    sess = Sess()
    error = ""
    try:
        worker._apply_value(page, by_label(fields, "Location"), "Gurgaon, India", "", sess, source="profile")
    except Exception as exc:
        error = str(exc)
    check("'Gurgaon, India' is Gurgaon - never 'Morinda, India', the one row sharing 'India'",
          page.evaluate("() => document.body.dataset.city || ''") == "Gurgaon", error or sess.lines)

    print("\n== a phone code the box shows short ==")
    sess = Sess()
    error = ""
    try:
        worker._apply_value(page, by_label(fields, "Phone Number"), "India +91", "", sess, source="")
    except Exception as exc:
        error = str(exc)
    check("'India +91' shown as 'IN +91' is a success, not 'not entered'",
          not error and page.evaluate("() => document.body.dataset.code") == "India +91", error)
    code = dict(by_label(fields, "Phone Number"), value="IN +91")
    check("  and is not reported as a wrong phone number",
          not worker._contact_warnings([code]), worker._contact_warnings([code]))

    print("\n== a choice that becomes a tag below an emptied box ==")
    college = by_label(fields, "College")
    sess = Sess()
    error = ""
    try:
        worker._apply_value(page, college, "Others", "", sess, source="")
    except Exception as exc:
        error = str(exc)
    check("'Others' is chosen", not error and page.evaluate("() => document.body.dataset.college") == "Others", error)
    check("  and its box reads empty, which the plan loop takes as the pick having gone in",
          worker._live_value(page, college) == "")
    b.close()

print()
if problems:
    print("MUI4 AUTOCOMPLETE CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("MUI4 AUTOCOMPLETE CHECK PASSED")
