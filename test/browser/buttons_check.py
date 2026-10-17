"""Buttons that send a form (#37, Oct 2026).

A <button> with no type submits its form, so the snapshot's `type` (the
attribute) read "Add another" as harmless while a click on it could send the
application. And words alone cannot tell a step's Continue from the one that
sends everything: one model call at the end of each step labels the buttons,
and the code keeps the last word on Submit / Send / Complete.

Part 1 drives the pieces against fixture_buttons.html; part 2 runs sessions.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import e2e_common as e2e  # noqa: E402  (isolation: scratch profile, history, Chrome)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, buttons, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

PAGE = (HERE / "fixture_buttons.html").as_uri()
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


def by_words(fields, text):
    return next((f for f in fields if buttons.words(f) == text), None)


print("== part 1: the pieces ==")
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.goto(PAGE + "#add")
    fields = browser.snapshot(page)
    add, plain, outside = (by_words(fields, t) for t in ("Add another", "Just a button", "Outside any form"))
    check("a <button> with no type inside a form is read as sending it",
          add is not None and add.get("submits") is True and not add.get("type"), add)
    check("type=button does not", plain is not None and plain.get("submits") is False, plain)
    check("nor does a button outside any form", outside is not None and outside.get("submits") is False, outside)

    sess = Sess()
    worker._click_holding_submit(browser.locate(page, add["id"], ""), add, sess)
    page.wait_for_timeout(200)
    state = page.evaluate("() => [document.querySelectorAll('#entries input').length, document.body.dataset.sent || '']")
    check("the agent's click on Add adds the entry and does not send the form", state == [2, ""], state)
    check("  and says it held the send off", any("held off" in line for line in sess.lines), sess.lines)

    page.goto(PAGE + "#add")
    page.reload()
    page.locator("#add").click()
    page.wait_for_timeout(200)
    check("(the replica is real: a plain click on it does send the form)",
          page.evaluate("() => document.body.dataset.sent || ''") == "yes")

    # The rules the model cannot change.
    final = {"id": 1, "tag": "button", "text": "Send application"}
    check("Send is plainly final", buttons.kind_of(final, {}) == "final")
    told_next = {1: buttons.ButtonVerdict(id=1, kind="next", confidence=0.99)}
    check("  and stays final when the model says next", buttons.kind_of(final, told_next) == "final")
    cont = {"id": 2, "tag": "button", "text": "Continue"}
    check("a Continue is next only when the model is sure",
          buttons.kind_of(cont, {2: buttons.ButtonVerdict(id=2, kind="next", confidence=0.9)}) == "next"
          and buttons.kind_of(cont, {2: buttons.ButtonVerdict(id=2, kind="next", confidence=0.5)}) == "")
    check("  and final at any confidence",
          buttons.kind_of(cont, {2: buttons.ButtonVerdict(id=2, kind="final", confidence=0.2)}) == "final")
    fields_one = [{"id": 5, "tag": "input", "type": "submit", "value": "Continue", "submits": True, "form": 0}]

    def sure_next(_system, _user, schema, last=False):
        return schema(buttons=[{"id": 5, "kind": "next", "confidence": 0.99}], last_page=last)

    on_step = buttons.classify(sure_next, fields_one, {}, 0, "x")
    on_last = buttons.classify(lambda s, u, sc: sure_next(s, u, sc, last=True), fields_one, {}, 0, "x")
    on_review = buttons.classify(sure_next, fields_one, {"says_review": True}, 0, "x")
    as_map = lambda r: {v.id: v for v in r.buttons}  # noqa: E731
    check("a sure 'next' on a step is acted on, but not on the last page or a review page",
          buttons.kind_of(fields_one[0], as_map(on_step)) == "next"
          and buttons.kind_of(fields_one[0], as_map(on_last)) == ""
          and buttons.kind_of(fields_one[0], as_map(on_review)) == "")
    in_footer = [{"id": 7, "tag": "button", "type": "button", "text": "Continue", "footer": True},
                 {"id": 8, "tag": "button", "type": "button", "text": "Help", "footer": True}]
    check("a modal's Continue in its own <footer> is labelled; the footer's other buttons are not",
          [f["id"] for f in buttons.candidates(in_footer)] == [7])
    check("a plain Next or Review needs no call; a Continue does",
          buttons.clearly_a_step({"text": "Next"}) and buttons.clearly_a_step({"text": "Review"})
          and buttons.clearly_a_step({"text": "Save and Continue"})
          and not buttons.clearly_a_step({"text": "Continue"}) and not buttons.clearly_a_step({"text": "Apply"}))
    check("the agent never clicks Complete, whatever its type",
          worker._never_click({"id": 3, "tag": "button", "type": "button", "text": "Complete"}))

    page.goto(PAGE + "#last")
    page.reload()
    context = buttons.read_context(browser.target(page))
    check("the page around the buttons is read: heading, counter, review wording",
          "Review your application" in context.get("headings", []) and context.get("counter") == "Step 4 of 4"
          and context.get("says_review") is True, context)
    b.close()


print("\n== part 2: sessions ==")
worker.ASK_BEFORE_ADVANCE = False


def session(mode, answers, verdicts):
    e2e.BUTTON_ANSWER.clear()
    e2e.BUTTON_ANSWER.update(verdicts)
    e2e.BUTTON_CALLS.clear()
    e2e.LLM_CALLS.clear()
    sess = e2e.ScriptedSession(answers)
    sess.t0 = time.time()
    job = {"job_id": f"test:buttons-{mode}", "company": "DummyCo", "title": "Engineer",
           "description": "Build things.", "apply_url": f"{PAGE}#{mode}"}
    worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                       resume_options=lambda: e2e.RESUME_OPTIONS, out_dir=e2e.SCRATCH)
    return sess, "\n".join(sess.logs), "\n".join(sess.asked)


print("\n-- a submit-type Continue the model calls a step --")
sess, logs, asked = session("step", ["applied"], {"continue": ("next", 0.9)})
check("one button call labels it", len(e2e.BUTTON_CALLS) >= 1, len(e2e.BUTTON_CALLS))
check("it is clicked as the next step", "'Continue' moves to the next step" in logs and "Clicked Continue" in logs,
      logs[-700:])
check("the next step's Submit application is left to the candidate",
      "Submit application" in asked and "Clicked Submit" not in logs, sess.asked)
check("the session ends as the candidate says", sess.status == "applied", sess.status)

print("\n-- the same Continue with no verdict: the old rule stands --")
sess, logs, asked = session("step", ["applied"], {})
check("not clicked: the candidate is pointed at it", "Clicked Continue" not in logs
      and "'Continue' yourself" in asked, sess.asked)

print("\n-- a type=button Continue on the review page that the model calls final --")
sess, logs, asked = session("last", ["applied"], {"continue": ("final", 0.6)})
check("not clicked", "Clicked Continue" not in logs, logs[-700:])
check("  and said why", "looks like it sends the application" in logs, logs[-700:])
check("  the candidate is told to click it", "'Continue' yourself" in asked, sess.asked)

print("\n-- the same page, model unsure: asked, even with auto next on --")
# It used to be clicked on the old word rules, unseen (code review, Oct 2026):
# a Continue that sends the application would have gone the same way.
sess, logs, asked = session("last", ["abort"], {"continue": ("next", 0.4)})
check("a low-confidence 'next' is not clicked unseen", "Clicked Continue" not in logs, logs[-700:])
check("  the candidate is asked, and told why", "I cannot tell whether 'Continue'" in asked, sess.asked)

print()
if problems:
    print("BUTTONS CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("BUTTONS CHECK PASSED")
