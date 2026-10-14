"""LinkedIn's redesigned Easy Apply radios (Bullet Microdrama, Oct 2026).

Every radio carries aria-label="<the question>", its <label for> is empty,
and Yes / No sit in a <p> beside the input's wrapper. Two things went wrong:

  - both options were read with the question as their label, so the model
    could not tell Yes from No;
  - the page ignored a synthetic click on the painted-out input, and three
    Yes/No questions each failed in nine seconds ("could not be ticked").

Replica only; nothing real is read or written.
"""

from __future__ import annotations

import sys
import time
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
        self.lines = []

    def log(self, line):
        self.lines.append(line)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 800})
    page.goto((HERE / "fixture_li_radio.html").as_uri())
    page.wait_for_timeout(200)

    print("== the options are read as Yes and No ==")
    fields = browser.snapshot(page)
    radios = [f for f in fields if f.get("type") == "radio"]
    check("four radios are seen", len(radios) == 4, str(len(radios)))
    labels = [str(f.get("label") or "") for f in radios]
    check("labelled Yes / No, not with the question",
          labels == ["Yes", "No", "Yes", "No"], str(labels))
    groups = {str(f.get("group") or "").rstrip("*") for f in radios}
    check("and each carries its question as the group",
          groups == {"Are you comfortable commuting to this job's location?",
                     "Are you comfortable working in Startup?"}, str(groups))

    print("\n== a synthetic click really is ignored by this page ==")
    page.evaluate("() => document.getElementById('_r_17_').click()")
    page.wait_for_timeout(100)
    check("the page undid it", page.evaluate("() => document.getElementById('_r_17_').checked") is False)

    print("\n== so the agent ticks it another way, fast ==")
    yes = next(f for f in radios if f["label"] == "Yes")
    sess = Sess()
    started = time.monotonic()
    try:
        worker._set_checked(page, browser.locate(page, yes["id"], str(yes.get("elid") or "")), yes, True)
        ticked = True
    except Exception as exc:
        ticked = False
        sess.log(f"refused: {exc}")
    took = time.monotonic() - started
    check("Yes is ticked", ticked and page.evaluate("() => document.getElementById('_r_s_').checked") is True,
          str(sess.lines))
    check("and the page accepted it", "_r_s_=true" in page.locator("#log").inner_text(),
          repr(page.locator("#log").inner_text()))
    check("in under four seconds", took < 4, f"{took:.1f}s")

    print("\n== a radio the page has taken away is reported at once ==")
    # Fourteen seconds per Yes on the real page: every rung timed out on an
    # element that was no longer there. Gone means the loop re-reads the page.
    gone = [f for f in radios if f["label"] == "No"][0]
    page.evaluate("() => document.getElementById('_r_t_').remove()")
    started = time.monotonic()
    try:
        worker._set_checked(page, browser.locate(page, gone["id"], str(gone.get("elid") or "")), gone, True)
        said = "ticked?"
    except worker.StaleField:
        said = "stale"
    except Exception as exc:
        said = f"other: {exc}"
    check("it is reported as gone", said == "stale", said)
    check("in under two seconds", time.monotonic() - started < 2, f"{time.monotonic() - started:.1f}s")

    print("\n== and one that will not take a tick says why ==")
    page.goto((HERE / "fixture_li_radio.html").as_uri())
    page.wait_for_timeout(200)
    page.evaluate("() => { const el = document.getElementById('_r_17_'); el.disabled = true; }")
    radios = [f for f in browser.snapshot(page) if f.get("type") == "radio"]
    stuck = [f for f in radios if f["label"] == "No"][1]
    try:
        worker._set_checked(page, browser.locate(page, stuck["id"], str(stuck.get("elid") or "")), stuck, True)
        why = "ticked?"
    except Exception as exc:
        why = str(exc)
    check("the refusal names what is wrong", "disabled" in why and "could not be ticked" in why, why)

    page.goto((HERE / "fixture_li_radio.html").as_uri())
    page.wait_for_timeout(200)
    radios = [f for f in browser.snapshot(page) if f.get("type") == "radio"]
    print("\n== the whole group answered through the model's action ==")
    # The usual path: a check action on the "No" of the second question.
    no = [f for f in radios if f["label"] == "No"][1]
    sess2 = Sess()
    worker._apply_value(page, no, "yes", "", sess2, wants_on=True)
    check("No is ticked in the second group",
          page.evaluate("() => document.getElementById('_r_17_').checked") is True)
    check("and the first group is untouched",
          page.evaluate("() => document.getElementById('_r_s_').checked") is False
          and page.evaluate("() => document.getElementById('_r_t_').checked") is False)
    check("the log names the option and its question",
          any("Checked" in line and "No" in line for line in sess2.lines), str(sess2.lines))

    b.close()

print()
if problems:
    print("LI RADIO CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("LI RADIO CHECK PASSED")
