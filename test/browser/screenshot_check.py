"""Pictures for the model, and the location rule (Oct 2026).

A box that turns its value down with a red message under it ("25 LPA" into a
digits-only CTC box became "25" and "Invalid input") goes back to the model
with a picture of the box and the message, and the box is emptied for the
corrected value. A field with no question words of its own is pictured at the
next model call; so is a refused dropdown (opened, so its options show), a box
the page marks invalid after Next, and a field the model asks to "look" at.
A model stand-in that takes no pictures gets none. A "which cities do you
prefer?" list is ticked whole when the profile's preferred_location is Any.

Part 1 drives the pieces on their own; part 2 is one whole session.
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

from src.apply import browser, profile, shots, worker  # noqa: E402
from src.apply.worker import ApplyAction, ApplyPlan  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

SHOTS = Path(tempfile.mkdtemp(prefix="oea_shots_"))
shots.SHOTS_DIR = SHOTS
data = json.loads(profile.PROFILE_PATH.read_text(encoding="utf-8"))
data["preferred_location"] = "Any"
profile.PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")

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


def field(fields, **want):
    return next(f for f in fields if all(f.get(k) == v for k, v in want.items()))


PIECES = """<!DOCTYPE html><html><body style="font-family:sans-serif;margin:24px">
<div class="row"><label for="age">Years with Java</label>
  <input id="age" type="text"><div class="msg" id="agemsg" style="color:#c00">This field is required</div></div>
<div class="row" style="margin-top:30px"><label for="mail">E-mail</label>
  <input id="mail" type="text" value="someone@" aria-invalid="true">
  <div class="error">Enter a valid e-mail address</div></div>
<div class="row" style="margin-top:30px"><label id="nl">Notice period</label>
  <button type="button" id="np" aria-haspopup="listbox" aria-controls="lb" aria-labelledby="nl">Select One</button>
  <ul role="listbox" id="lb" hidden style="list-style:none;border:1px solid #888;width:200px;padding:4px">
    <li role="option">Immediate</li><li role="option">15 days</li><li role="option">30 days</li>
    <li role="option">60 days</li><li role="option">90 days</li></ul></div>
<div class="row" style="margin-top:30px" role="radiogroup" aria-labelledby="locq">
  <span id="locq">Are you located in Vadodara, Gujarat? *</span>
  <label><input type="radio" name="loc" value="1" aria-invalid="true"> Yes</label>
  <label><input type="radio" name="loc" value="0" aria-invalid="true"> No</label>
  <div class="error">Answer this question</div></div>
<fieldset style="margin-top:30px"><legend class="drawnq"></legend>
  <input type="radio" name="drawn" id="d1"><label for="d1">Yes</label>
  <input type="radio" name="drawn" id="d2"><label for="d2">No</label></fieldset>
<style>.drawnq::before { content: "Do you hold a driving licence?"; }</style>
<script>
  document.getElementById('np').onclick = () => { const l = document.getElementById('lb'); l.hidden = !l.hidden; };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('lb').hidden = true; });
  for (const li of document.querySelectorAll('li')) li.onclick = () => {
    document.getElementById('np').textContent = li.textContent; document.getElementById('lb').hidden = true; };
</script></body></html>"""

print("== part 1: the pieces ==")
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto((HERE / "fixture_screenshots.html").as_uri())
    fields = browser.snapshot(page)

    # A value the box turns down, with the message under it.
    years = field(fields, elid="pyyears")
    sess = Sess()
    started = time.monotonic()
    try:
        worker._apply_value(page, years, "5 years", "", sess, source="profile")
        raised = None
    except worker.FieldRejected as exc:
        raised = exc
    took = time.monotonic() - started
    check("the refused value raises FieldRejected", raised is not None, sess.lines)
    check("naming the page's message", raised is not None and "Invalid input - numbers only" in str(raised),
          str(raised))
    check("and what the box made of it", raised is not None and "showed '5'" in str(raised), str(raised))
    check("with a picture", raised is not None and (raised.png or b"")[:4] == b"\x89PNG")
    if raised is not None and raised.png:
        w, h = shots.size(raised.png)
        check("the picture is a crop, with room for the label and the message", 150 < w <= shots.MAX_WIDTH
              and 100 < h <= shots.MAX_HEIGHT, f"{w}x{h}")
    check("the box is emptied for the corrected value", page.locator("#pyyears").input_value() == "")
    check("without the five-second suggestion hunt", took < 1.5, f"{took:.2f}s")
    # The refusal goes into the session's memory with its picture.
    worker._remember_refusal(sess, "years key", "5 years", raised)
    check("the refusal is waiting as a picture for the next call",
          "years key" in worker._looks(sess) and worker._refusals(sess)["years key"][0] == "5 years")

    # The corrected value goes in quietly.
    sess = Sess()
    worker._apply_value(page, years, "5", "", sess, source="model")
    check("the corrected value is kept", page.locator("#pyyears").input_value() == "5", sess.lines)

    # Unclear: nothing in the DOM says what q7 asks (CSS draws the question).
    q7 = field(fields, elid="q7")
    check("a box with no question words is unclear", worker._unclear(q7), q7)
    check("one with a label is not", not worker._unclear(years))
    cities = [f for f in fields if f.get("name") == "city"]
    check("nor an option under a question", not any(worker._unclear(f) for f in cities))

    # The location rule, through the real sweep.
    sess = Sess()
    handled: set[str] = set()
    worker._sweep(page, cities, handled, {}, {"company": "X"}, "", sess)
    ticked = page.evaluate("() => [...document.querySelectorAll('input[name=city]')].filter(i => i.checked).map(i => i.value)")
    check("Any ticks every city offered", ticked == ["blr", "pune", "hyd"], ticked)
    check("but not 'None of the above'", "none" not in ticked)

    # The pictures for a call: q7 is unclear and still open.
    sess = Sess()
    sess.company = "Pieces Co"
    pictures = worker._pictures_for_call(page, fields, set(), sess, [q7])
    check("the unclear box is pictured for the call", len(pictures) == 1 and "does not say" in pictures[0][1],
          [p[1] for p in pictures])
    saved = list((SHOTS / "Pieces-Co").glob("*.png"))
    check("and kept under screenshots/<company>/", len(saved) == 1, saved)
    again = worker._pictures_for_call(page, fields, set(), sess, [q7])
    check("once a session", again == [])

    # Part 1b: the stand-alone page - message before the fill, invalid
    # marks after Next, a refused dropdown, the model's look.
    page.set_content(PIECES)
    fields = browser.snapshot(page)
    java = field(fields, elid="age")
    sess = Sess()
    worker._apply_value(page, java, "4", "", sess, source="model")
    check("a message already there before the fill does not count", page.locator("#age").input_value() == "4",
          sess.lines)

    sess = Sess()
    handled = {worker._field_key(f, worker._field_label(f)) for f in fields}
    written = {worker._field_key(field(fields, elid="mail"), "E-mail"): "someone@"}
    flagged = worker._flag_invalid(page, fields, handled, written, sess, "Next")
    mail_key = worker._field_key(field(fields, elid="mail"), worker._field_label(field(fields, elid="mail")))
    check("the box and the Yes / No question marked invalid after Next are flagged: two, not three",
          flagged == 2, sess.lines)
    # Indeed (Oct 2026): "Are you located in Vadodara?" was two pictures, a
    # crop round Yes and another round No. One question, one picture.
    loc = [f for f in fields if f.get("name") == "loc"]
    looks = worker._looks(sess)
    check("the question is ONE picture, keyed by the question", "question:loc" in looks
          and not any(worker._field_key(f, worker._field_label(f)) in looks for f in loc), list(looks))
    if "question:loc" in looks:
        item = looks["question:loc"]
        check("  named by its question, not 'Yes'", item["label"].startswith("Are you located in Vadodara"),
              item["label"])
        boxes = [browser.locate(page, f["id"], "").bounding_box() for f in loc]
        w, _h = shots.size(item["png"])
        check("  with both options in it", w >= boxes[1]["x"] + boxes[1]["width"] - boxes[0]["x"],
              f"{w}px wide; options {boxes[0]['x']:.0f}..{boxes[1]['x'] + boxes[1]['width']:.0f}")
    check("  one line in the transcript for it",
          sum("Are you located in Vadodara" in line for line in sess.lines) == 1, sess.lines)
    check("  and both options are opened again for the model",
          all(worker._field_key(f, worker._field_label(f)) not in handled for f in loc))
    check("its message is read", any("Enter a valid e-mail address" in line for line in sess.lines), sess.lines)
    check("it is opened again for the model", mail_key not in handled and mail_key not in written)
    check("emptied, with its value counted as refused",
          page.locator("#mail").input_value() == "" and worker._refusals(sess)[mail_key][0] == "someone@")
    check("and pictured", mail_key in worker._looks(sess))

    # A refused dropdown is opened for its picture, then closed.
    np = field(fields, elid="np")
    np_key = worker._field_key(np, worker._field_label(np))
    sess = Sess()
    worker._refusals(sess)[np_key] = ("Two months", "matches none")
    closed = shots.size(shots.crop(page, [browser.locate(page, np["id"], "np")]) or b"")[1]
    pictures = worker._pictures_for_call(page, fields, set(), sess, [np])
    check("a refused dropdown is pictured open", len(pictures) == 1 and "opened" in pictures[0][1],
          [p[1] for p in pictures])
    if pictures:
        check("with its options in the picture", shots.size(pictures[0][2])[1] > closed + 60,
              f"{shots.size(pictures[0][2])} vs closed height {closed}")
    check("and closed again", page.locator("#lb").is_hidden())

    # The model's look.
    sess = Sess()
    notes: list[str] = []
    look = ApplyAction(action="look", field_id=java["id"])
    got = worker._run_action(page, look, fields, {}, "", sess, [], notes, set(), {}, {}, set())
    java_key = worker._field_key(java, worker._field_label(java))
    check("look takes a picture and plans again", got == "refused" and java_key in worker._looks(sess), got)
    worker._pictures_for_call(page, fields, set(), sess, [])
    got = worker._run_action(page, look, fields, {}, "", sess, [], notes, set(), {}, {}, set())
    check("a second look at the same field is refused", got == "skipped" and "already sent a picture" in notes[-1],
          notes[-1:])
    check("look never pauses for the candidate", not worker._pauses_for_user(look, fields))

    # Yes / No with the question drawn by CSS: both options are unclear, and
    # they are one question - one picture, naming both ids.
    drawn = [f for f in fields if f.get("name") == "drawn"]
    check("(both drawn options are unclear)", len(drawn) == 2 and all(worker._unclear(f) for f in drawn),
          [(f.get("label"), f.get("group")) for f in drawn])
    sess = Sess()
    pictures = worker._pictures_for_call(page, fields, set(), sess, drawn)
    check("an unclear Yes / No question is one picture, not one per option", len(pictures) == 1,
          [p[1] for p in pictures])
    if pictures:
        check("  its reason names both options' ids",
              all(str(f["id"]) in pictures[0][1] for f in drawn), pictures[0][1])
    # The model's look at Yes, then at No: one picture.
    sess = Sess()
    notes = []
    first = worker._run_action(page, ApplyAction(action="look", field_id=drawn[0]["id"]), fields, {}, "", sess,
                               [], notes, set(), {}, {}, set())
    second = worker._run_action(page, ApplyAction(action="look", field_id=drawn[1]["id"]), fields, {}, "", sess,
                                [], notes, set(), {}, {}, set())
    check("look at Yes, then at No: the second is already covered",
          first == "refused" and second == "skipped" and len(worker._looks(sess)) == 1, (first, second, notes))
    b.close()


print("\n== part 2: one session ==")
CALLS: list[tuple[str, list[bytes]]] = []


def model(system, user, schema, images=None):
    CALLS.append((user, list(images or [])))
    fields = json.loads(user.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0])
    actions = []
    for f in fields:
        if f.get("already_handled") or f.get("value"):
            continue
        if f.get("elid") == "q7":
            actions.append(ApplyAction(action="fill", field_id=f["id"], value="Bachelors", confidence=0.95))
        elif f.get("elid") == "pyyears":
            fixed = "did not accept '5 years'" in user
            actions.append(ApplyAction(action="fill", field_id=f["id"], value="5" if fixed else "5 years",
                                       confidence=0.95))
    return ApplyPlan(actions=actions)


model.takes_images = True
worker.make_invoker = lambda cfg, env, purpose: model
sess = e2e.ScriptedSession(["applied"])
sess.t0 = time.time()
job = {"job_id": "test:shots", "company": "DummyCo", "title": "Software Engineer",
       "description": "Build things.", "apply_url": (HERE / "fixture_screenshots.html").as_uri()}
worker.run_session(sess, job, "dummy resume text", AppConfig(), load_env(), headless=True,
                   resume_options=lambda: e2e.RESUME_OPTIONS, out_dir=e2e.SCRATCH)
check("the session ends applied", sess.status == "applied", sess.status)
check("two model calls", len(CALLS) == 2, len(CALLS))
if len(CALLS) == 2:
    first, second = CALLS
    check("call 1 carries the unclear box's picture", len(first[1]) == 1
          and "PICTURES (attached" in first[0] and "does not say what it asks" in first[0])
    check("call 2 carries the refused box's picture and message", len(second[1]) == 1
          and "Invalid input - numbers only" in second[0], second[0].split("PICTURES", 1)[-1][:200])
    check("and not call 1's picture again", "does not say what it asks" not in second[0])
logs = "\n".join(sess.logs)
check("the corrected value went in", "Filled Years of experience with Python * = 5\n" in logs + "\n", logs[-600:])
check("every city ticked from the profile",
      all(f"Checked {c}" in logs for c in ("Bengaluru", "Pune", "Hyderabad")) and "Checked None" not in logs)
saved = sorted(p.name for p in (SHOTS / "DummyCo").glob("*.png"))
check("both pictures kept under screenshots/DummyCo/", len(saved) == 2, saved)

print()
if problems:
    print("SCREENSHOT CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("SCREENSHOT CHECK PASSED")
