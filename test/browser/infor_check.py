"""Infor's form (Oct 14 2026): five Yes/No questions under one heading, and
an older react-select Country box under an accessibility overlay.

What went wrong on the real page:
  - "no" to the relatives question ticked "Are you at least 18 years old?
    no", three times: every radio shared the group "3.Questions", so all five
    questions were one ten-option question, and one cached "no" was replayed
    into each of them;
  - "India" typed at Country left "British Indian Ocean Territory" first and
    "India" second; neither row was read (no role on them), Enter took the
    first, and the refusal left it on the form;
  - the box was called "Text field", the overlay's label, not "Country";
  - the dropdown call that picks the nearest option was never made: it is
    given the options read off the list, and none were read.

Part 1 drives the pieces against the replica; part 2 is whole sessions on
each kind of list: rows marked as options, plain rows read by where they
are, and rows with no text at all, which the dropdown call reads off a
picture (and, when it cannot, the planning model gets that picture before
the candidate is asked anything).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import e2e_common as e2e  # noqa: E402  (isolation: scratch profile, history, Chrome)
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, matching, shots, worker  # noqa: E402
from src.apply.worker import ApplyAction, ApplyPlan  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

shots.SHOTS_DIR = Path(tempfile.mkdtemp(prefix="oea_infor_shots_"))
URL = (HERE / "fixture_infor.html").as_uri()
POSTING = (HERE / "fixture_infor_posting.html").as_uri()
problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self, replies=()):
        self.lines: list[str] = []
        self.replies = list(replies)
        self.asked: list[str] = []

    def log(self, line):
        self.lines.append(line)

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        return self.replies.pop(0)


def radios(fields):
    return [f for f in fields if (f.get("type") or "").lower() == "radio"]


def ticked(page):
    return page.evaluate("() => [...document.querySelectorAll('input[type=radio]')]"
                         ".filter(r => r.checked).map(r => r.id.replace(/^.*attributes_/, ''))")


def country(page):
    return page.evaluate("() => [(document.getElementById('single') || {}).textContent || '',"
                         " document.getElementById('country-code').value]")


RELATIVES = "Do you have relatives"
NON_COMPETE = "Have you at any time signed"

print("== part 1: the pieces ==")
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(URL)
    fields = browser.snapshot(page)

    box = next(f for f in fields if f.get("role") == "combobox")
    check("the overlay's 'Text field' gives way to the Country label", box.get("label") == "Country",
          box.get("label"))

    options = radios(fields)
    groups = sorted({f.get("group") for f in options})
    check("five questions, each its own group", len(groups) == 5, groups)
    check("each option is just Yes or No", sorted({f.get("label") for f in options}) == ["No", "Yes"],
          sorted({f.get("label") for f in options}))
    rel = [f for f in options if str(f.get("group")).startswith(RELATIVES)]
    check("even the question too long for a label", [f.get("label") for f in rel] == ["Yes", "No"],
          [(f.get("label"), f.get("group")[-20:]) for f in rel])
    check("and the 18+ question is not merged into it",
          not any("18 years" in str(f.get("group")) for f in rel))

    # The relatives question asked, "no" answered: only ITS No is ticked.
    sess = Sess(["no"])
    notes: list[str] = []
    answers_cache: dict[str, str] = {}
    yes = next(f for f in rel if f.get("label") == "Yes")
    no = next(f for f in rel if f.get("label") == "No")
    check("the radio is under its own label, as on the real page",
          worker._under_own_label(browser.locate(page, no["id"], str(no.get("elid") or ""))))
    ask = ApplyAction(action="ask", field_id=yes["id"], question="Relatives at Infor?", confidence=0.9)
    started = time.time()
    worker._run_action(page, ask, fields, {"company": "Infor"}, "", sess, [], notes, set(), {},
                       answers_cache, set())
    took = time.time() - started
    check("'no' ticks the relatives question's No", ticked(page) == ["2_boolean_answer_false"],
          ticked(page))
    check("  at once, not after a 4 s wait for the label to move", took < 1.5, f"{took:.2f}s")
    check("  and the transcript names the question",
          any(line.startswith("Checked No - Do you have relatives") for line in sess.lines), sess.lines)
    # The non-compete question asked next: asked, not answered with that "no".
    nc_yes = next(f for f in options if str(f.get("group")).startswith(NON_COMPETE) and f.get("label") == "Yes")
    sess2 = Sess(["yes"])
    ask2 = ApplyAction(action="ask", field_id=nc_yes["id"], question="Non-compete?", confidence=0.9)
    worker._run_action(page, ask2, browser.snapshot(page), {"company": "Infor"}, "", sess2, [], notes, set(), {},
                       answers_cache, set())
    check("the next question is asked, not answered with the last one's reply", sess2.asked == ["Non-compete?"],
          sess2.asked)
    check("and its own answer ticks its own option",
          sorted(ticked(page)) == ["2_boolean_answer_false", "3_boolean_answer_true"], ticked(page))
    check("the 18+ question is untouched", not any(t.startswith("0_") for t in ticked(page)), ticked(page))

    # Country: "India" is the second row of what "India" leaves.
    page.goto(URL)
    fields = browser.snapshot(page)
    box = next(f for f in fields if f.get("role") == "combobox")
    sess = Sess()
    worker._apply_value(page, box, "India", "", sess, source="profile")
    check("'India' is picked, not British Indian Ocean Territory", country(page) == ["India", "IN"],
          f"{country(page)} {sess.lines}")

    # Rows nothing marks as options: read by where they are, under the box.
    page.goto(URL + "#plain")
    page.reload()
    fields = browser.snapshot(page)
    box = next(f for f in fields if f.get("role") == "combobox")
    sess = Sess()
    worker._apply_value(page, box, "India", "", sess, source="profile")
    check("plain rows under the box are read: 'India' is picked by name", country(page) == ["India", "IN"],
          f"{country(page)} {sess.lines}")
    check("  without Enter", not any("keyboard" in line for line in sess.lines), sess.lines)

    # Rows with no text to read at all: Enter takes the first row, which is
    # refused AND taken back out.
    page.goto(URL + "#drawn")
    page.reload()
    fields = browser.snapshot(page)
    box = next(f for f in fields if f.get("role") == "combobox")
    sess = Sess()
    try:
        worker._apply_value(page, box, "India", "", sess, source="profile")
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("a wrong row taken by Enter is refused", "British Indian Ocean Territory" in refused, refused)
    check("  and taken back out of the box", country(page) == ["", ""], country(page))
    check("  and the transcript says so", any("back out" in line for line in sess.lines), sess.lines)

    # The picture of that list has its rows in it, though no text read does.
    png = worker._look_at(page, box, open_list=True, typed="India")
    height = shots.size(png)[1] if png else 0
    check("its picture takes in the room below the box, where the list is", height >= 300, height)
    check("  and leaves the box empty and closed",
          country(page) == ["", ""] and page.evaluate("() => !document.querySelector('.react-select__menu')"))

    # A row named off a picture is taken by the keyboard and checked.
    sess = Sess()
    worker._pick_row_by_keys(page, box, "India", 2, "India", sess)
    check("row 2 of the pictured list is India, taken by the keyboard", country(page) == ["India", "IN"],
          f"{country(page)} {sess.lines}")
    check("  first time, nothing taken back", not any("back out" in line for line in sess.lines), sess.lines)
    page.evaluate("() => clearChoice()")
    sess = Sess()
    try:
        worker._pick_row_by_keys(page, box, "India", 2, "Indonesia", sess)
        wrong = ""
    except ValueError as exc:
        wrong = str(exc)
    check("a row that does not give the option read off the picture is refused",
          "did not give 'Indonesia'" in wrong, wrong)
    check("  and nothing it took is left in the box", country(page) == ["", ""], f"{country(page)} {sess.lines}")

    # A choice the box already held is never cleared by a refusal.
    page.evaluate("() => { const o = COUNTRIES.find(c => c[0] === 'IN'); choose(o); }")
    check("  (the box now holds India, put there by hand)", country(page) == ["India", "IN"])
    note = worker._undo_wrong_choice(page, browser.locate(page, box["id"], str(box.get("elid") or "")),
                                     "India", "India", Sess(), "", "Country")
    check("  a choice that was there before is left alone", note == "" and country(page) == ["India", "IN"],
          country(page))

    # Lists the loose read must NOT take: nothing open, and an open list
    # naming nothing of what was typed.
    page.goto(URL + "#plain")
    page.reload()
    fields = browser.snapshot(page)
    box = next(f for f in fields if f.get("role") == "combobox")
    loc = browser.locate(page, box["id"], str(box.get("elid") or ""))
    _rows, texts = worker._visible_options(page, loc)
    check("no list open: no rows, though the radios sit below the box", texts == [], texts)
    loc.click()
    loc.fill("India")
    _rows, texts = worker._visible_options(page, loc)
    check("'India' typed: the two rows naming it", texts == ["British Indian Ocean Territory", "India"], texts)
    page.evaluate("() => { document.querySelectorAll('.opt').forEach(o => o.textContent = 'Zambia'); }")
    _rows, texts = worker._visible_options(page, loc)
    check("rows naming nothing of what was typed are not its list", texts == [], texts)

    # Enter in a dropdown with nothing highlighted falls through to the form:
    # the guard stops both the default button's click and the submit, and
    # says so. A form that sends by the button's own click handler too.
    page.set_content("""<form id="f" onsubmit="window.sent = 'submit'; return false">
      <input id="box" role="combobox" aria-expanded="true" value="Ind">
      <button type="submit" onclick="window.clicked = true">Submit application</button></form>
      <script>window.sent = ''; window.clicked = false;</script>""")
    box_loc = page.locator("#box")
    try:
        worker._press_enter(box_loc)
        guard = ""
    except ValueError as exc:
        guard = str(exc)
    check("Enter that would send the form is stopped, and said so", "fell through to the form" in guard, guard)
    check("  nothing was sent, by submit or by the button's click",
          page.evaluate("() => [window.sent, window.clicked]") == ["", False],
          page.evaluate("() => [window.sent, window.clicked]"))
    page.evaluate("() => document.querySelector('button').click()")
    check("  and the guard is gone afterwards (the candidate's own click still works)",
          page.evaluate("() => window.sent") == "submit")
    # A widget that takes Enter itself (preventDefault) is not disturbed.
    page.set_content("""<form onsubmit="window.sent = 'submit'; return false">
      <input id="box" role="combobox" aria-expanded="true">
      <button type="submit">Submit application</button></form>
      <script>window.sent = ''; document.getElementById('box').addEventListener('keydown',
        e => { if (e.key === 'Enter') { e.preventDefault(); window.picked = true; } });</script>""")
    worker._press_enter(page.locator("#box"))
    check("  a widget that takes Enter itself goes on as before",
          page.evaluate("() => [window.sent, !!window.picked]") == ["", True])

    # The job's page: "Apply Now" is a plain link to the form.
    page.goto(POSTING)
    fields = browser.snapshot(page)
    found = worker._apply_link(page, fields, [])
    check("the job page's Apply Now link is found, with the form's address",
          found is not None and found[0].get("label") == "Apply Now" and found[1] == URL,
          found and (found[0].get("label"), found[1]))
    check("  but not once anything has been done in the session",
          worker._apply_link(page, fields, ["filled First Name"]) is None)
    page.goto(POSTING + "#hash-only")
    page.reload()
    check("  nor when it goes nowhere (href='#'), a button by another name",
          worker._apply_link(page, browser.snapshot(page), []) is None)
    b.close()


print("\n== part 2: whole sessions ==")
CALLS: list[tuple[str, int]] = []      # the planning calls: (prompt, pictures)
PICKS: list[tuple[str, int]] = []      # the dropdown calls
PICK_ANSWER: list = []                 # what the dropdown call answers, set per session


def plan_for(fields, user):
    actions = []
    for f in fields:
        if f.get("already_handled") or (f.get("type") or "").lower() != "radio" or f.get("label") != "Yes":
            continue
        group = str(f.get("group") or "")
        if group.startswith("Are you at least 18"):
            actions.append(ApplyAction(action="check", field_id=f["id"], confidence=0.95))
        elif group.startswith((RELATIVES, NON_COMPETE)):
            actions.append(ApplyAction(action="ask", field_id=f["id"], question=group[:60], confidence=0.9))
    return actions


def picker(user, images):
    """The dropdown call: reads the pictured list as a model would."""
    PICKS.append((user, len(images or [])))
    return matching.OptionPicks(picks=list(PICK_ANSWER))


def model(system, user, schema, images=None):
    if schema is matching.OptionPicks:
        return picker(user, images)
    CALLS.append((user, len(images or [])))
    fields = json.loads(user.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0])
    return ApplyPlan(actions=plan_for(fields, user))


model.takes_images = True
worker.make_invoker = lambda cfg, env, purpose: model


def session(url, replies, pick=()):
    CALLS.clear()
    PICKS.clear()
    PICK_ANSWER[:] = list(pick)
    sess = e2e.ScriptedSession(replies)
    sess.t0 = time.time()
    job = {"job_id": "test:infor", "company": "Infor", "title": "Software Engineer",
           "description": "Build things.", "apply_url": url}
    worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                       resume_options=lambda: e2e.RESUME_OPTIONS, out_dir=e2e.SCRATCH)
    return sess


print("\n-- readable list --")
sess = session(URL, ["no", "yes", "applied"])
logs = "\n".join(sess.logs)
check("the session ends applied", sess.status == "applied", sess.status)
check("Country filled from the profile, as India", "Selected 'India' for Country" in logs, logs[:400])
check("each question asked once, in its own words",
      [q[:20] for q in sess.asked[:2]] == [RELATIVES[:20], NON_COMPETE[:20]], [q[:30] for q in sess.asked])
check("no answer ticked another question's option",
      "selects 'Are you at least 18" not in logs and "Checked No" in logs, logs[-900:])

check("no dropdown call: nothing was refused", PICKS == [], len(PICKS))

print("\n-- from the job's page --")
started = time.time()
sess = session(POSTING, ["no", "yes", "applied"])
logs = "\n".join(sess.logs)
check("the Apply Now link is followed, not handed to the model",
      "'Apply Now' is a link to the application form - opened it" in logs, logs[:500])
check("no model call about the job page: the first is about the form",
      bool(CALLS) and "Country" in CALLS[0][0] and "Register Your Interest" not in CALLS[0][0],
      CALLS[0][0][:300] if CALLS else "no calls")
check("the session ends applied", sess.status == "applied", sess.status)

print("\n-- plain rows --")
sess = session(URL + "#plain", ["no", "yes", "applied"])
logs = "\n".join(sess.logs)
check("the session ends applied", sess.status == "applied", sess.status)
check("Country filled from the profile, as India, off the rows read by place",
      "Selected 'India' for Country" in logs and "back out" not in logs, logs[:400])
check("no dropdown call and no picture: nothing was refused",
      PICKS == [] and all(n == 0 for _u, n in CALLS), (len(PICKS), [n for _u, n in CALLS]))

print("\n-- drawn rows: the dropdown call reads them off a picture --")
sess = session(URL + "#drawn", ["no", "yes", "applied"],
               pick=[matching.OptionPick(ref=1, option="India", match="exact", position=2,
                                         reason="same country")])
logs = "\n".join(sess.logs)
check("the profile's India is refused, and taken back out", "back out" in logs, logs[:600])
check("one dropdown call, with the list's picture", [n for _u, n in PICKS] == [1], [n for _u, n in PICKS])
check("  told the options are in the picture, not given an empty list",
      bool(PICKS) and "in picture 1" in PICKS[0][0], PICKS[0][0] if PICKS else "")
check("  and India goes in by its row", "row 2 of its pictured list" in logs, logs[:900])
check("the session ends applied", sess.status == "applied", sess.status)
check("the planning model never needed a picture of Country",
      all(n == 0 for _u, n in CALLS), [n for _u, n in CALLS])
check("each question asked once", [q[:20] for q in sess.asked[:2]] == [RELATIVES[:20], NON_COMPETE[:20]],
      [q[:30] for q in sess.asked])

print("\n-- drawn rows the dropdown call cannot read --")


def model_unreadable(system, user, schema, images=None):
    if schema is matching.OptionPicks:
        return picker(user, images)
    CALLS.append((user, len(images or [])))
    fields = json.loads(user.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0])
    actions = plan_for(fields, user)
    box = next((f for f in fields if f.get("role") == "combobox" and not f.get("value")), None)
    if box is not None and len(CALLS) == 1:
        # A real model's other spelling of the refused value, which this
        # list cannot take either: the questions after it must wait.
        actions.insert(0, ApplyAction(action="select", field_id=box["id"], value="Republic of India",
                                      confidence=0.9))
    return ApplyPlan(actions=actions)


model_unreadable.takes_images = True
worker.make_invoker = lambda cfg, env, purpose: model_unreadable
sess = session(URL + "#drawn", ["no", "yes", "applied"],
               pick=[matching.OptionPick(ref=1, option="", match="none", reason="cannot read it")])
logs = "\n".join(sess.logs)
check("the profile's India is refused, and taken back out", "back out" in logs, logs[:600])
check("the dropdown call saw the picture and declined", [n for _u, n in PICKS] == [1], [n for _u, n in PICKS])
check("the first plan gets that same picture, not a new one",
      bool(CALLS) and CALLS[0][1] == 1 and "could not be read as text" in CALLS[0][0],
      [c[1] for c in CALLS])
check("the questions wait for a second plan after the dropdown fails", len(CALLS) >= 2, len(CALLS))
check("that plan carries a picture of the opened list",
      len(CALLS) >= 2 and CALLS[1][1] >= 1 and "opened" in CALLS[1][0], [c[1] for c in CALLS])
check("the questions are still asked, once each",
      [q[:20] for q in sess.asked[:2]] == [RELATIVES[:20], NON_COMPETE[:20]], [q[:30] for q in sess.asked])

print()
if problems:
    print("INFOR CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("INFOR CHECK PASSED")
