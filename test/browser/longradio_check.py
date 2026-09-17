"""One question must not spend the whole field budget.

The USP application on UKG/UltiPro, Sep 17 2026. "What is your country of
origin?" is 46 radios sharing a name; MAX_FIELDS is 60, and they took every
slot from 46 on. The six REQUIRED questions below them - and the form's own
Submit button - reached nothing: not the model, not the profile, not the llm:
flow. The session log reads:

  239.6s Checked India
  239.6s FILL THIS ONE YOURSELF: Argentina - required, and I could not work
         out what to put in it.
  ...thirteen more...
  326.6s YOU: llm : Have you ever been terminated, discharged, or asked to
         resign from any position?
  326.8s Asking the model about 0 field(s)...
  335.6s Model returned 0 action(s).

Every line of that is this one defect. The candidate's summary - "nothing was
filled by system on this page" - was exactly right.

Scratch profile, temp DB. Nothing real is read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "phone_country_code": "+91",
}), encoding="utf-8")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class Sess:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


QUESTIONS = (
    "Have you ever been employed by USP?",
    "Have you ever been terminated, discharged, or asked to resign",
    "What is your experience in Full Stack development",
    "What is your current CTC",
    "What are your salary expectations for this position?",
    "What is your notice period",
)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto((HERE / "fixture_longradio.html").as_uri())
    page.wait_for_timeout(250)

    print("the page really is the one that failed")
    raw = browser.target(page, refresh=True).evaluate(browser.SNAPSHOT_JS)
    # If the page fitted inside the budget there would be nothing to fix and
    # everything below would pass on a form that was never truncated.
    check("it does not fit in MAX_FIELDS", len(raw) > browser.MAX_FIELDS,
          f"{len(raw)} controls, budget {browser.MAX_FIELDS}")
    cut = raw[:browser.MAX_FIELDS]
    cut_labels = " | ".join(str(f.get("label") or "") for f in cut)
    check("and a head-of-the-list cut loses every question",
          not any(q[:24] in cut_labels for q in QUESTIONS))
    check("and loses the Submit button too",
          not any((f.get("label") or f.get("text") or "") == "Submit" for f in cut))

    fields = browser.snapshot(page)
    by_label = {str(f.get("label") or ""): f for f in fields}
    print(f"\n{len(fields)} fields after the collapse")

    print("\nthe questions below the group are there now")
    for q in QUESTIONS:
        check(f"{q[:40]!r} is in the snapshot",
              any(q[:24] in lab for lab in by_label))
    check("and so is Submit",
          any((f.get("label") or f.get("text") or "") == "Submit" for f in fields))

    print("\nand when the budget still bites, it says so")
    # Collapsing the group bought a lot of headroom, but a big enough form
    # does not fit and the cut used to be silent - which is how six required
    # questions and the Submit button went missing while the session reported,
    # accurately and uselessly, that it had nothing left to do.
    check("nothing is dropped once the group is collapsed",
          browser.last_dropped() == 0, str(browser.last_dropped()))
    page.evaluate("""(n) => { const f = document.querySelector('form');
        for (let i = 0; i < n; i++) {
          const w = document.createElement('div');
          w.innerHTML = '<label for="pad' + i + '">Padding ' + i +
                        '</label><input id="pad' + i + '" type="text">';
          f.appendChild(w);
        } }""", 40)
    page.wait_for_timeout(150)
    padded = browser.snapshot(page)
    check("a form too big to hold is capped", len(padded) == browser.MAX_FIELDS,
          str(len(padded)))
    check("and the overflow is reported, not swallowed",
          browser.last_dropped() > 0, str(browser.last_dropped()))
    page.goto((HERE / "fixture_longradio.html").as_uri())
    page.wait_for_timeout(250)
    browser.snapshot(page)
    check("and the report clears when it all fits again",
          browser.last_dropped() == 0, str(browser.last_dropped()))

    print("\nthe long group is one field that knows its own question")
    group = [f for f in fields if f.get("group_ids")]
    check("exactly one field stands for the group", len(group) == 1, f"{len(group)} found")
    origin = group[0] if group else {}
    check("it is labelled with the question, not a country",
          origin.get("label") == "What is your country of origin?",
          repr(origin.get("label")))
    check("and it carries all 46 options",
          len(origin.get("options") or []) == 46,
          str(len(origin.get("options") or [])))
    check("and reads as unanswered while it is",
          not (origin.get("value") or "") and origin.get("checked") is False,
          f"value={origin.get('value')!r} checked={origin.get('checked')!r}")

    print("\na SHORT group is untouched - every Yes/No on every form")
    referral = [f for f in fields
                if (f.get("name") or "") == "employeereferral"]
    check("both options are still their own field", len(referral) == 2,
          f"{len(referral)} found")
    check("and they carry the question",
          all(f.get("group") == "Were you referred by a current employee?"
              for f in referral),
          str([f.get("group") for f in referral]))
    check("and no option list was invented for them",
          not any(f.get("group_ids") for f in referral))

    print("\nthe answer goes to the option it names, not the stand-in")
    # The stand-in for an unanswered group is its FIRST option. Ticking the
    # field itself would answer "Argentina" to a question whose answer is
    # India - a wrong answer the candidate is told is right, on a required
    # field.
    sess = Sess()
    worker._apply_value(page, origin, "India", "", sess, source="profile")
    ticked = page.evaluate(
        "() => { const e = document.querySelector("
        "'input[name=MultipleChoiceResponse0]:checked');"
        " return e ? e.closest('label').innerText.trim() : ''; }")
    check("the form received India", ticked == "India", repr(ticked))
    check("and the transcript says so",
          any("India" in line and "country of origin" in line for line in sess.lines),
          str(sess.lines))

    print("\nan answered group reads as answered")
    answered = next(f for f in browser.snapshot(page) if f.get("group_ids"))
    check("its value is the chosen option", answered.get("value") == "India",
          repr(answered.get("value")))
    check("and nothing asks about it again",
          not any(f.get("group_ids")
                  for f in worker._unresolved_fields(browser.snapshot(page), set())))

    print("\nan option that is not on offer is refused, never approximated")
    try:
        worker._apply_value(page, origin, "Atlantis", "", Sess())
        took = True
    except Exception as exc:
        took, why = False, str(exc)
    check("a country the form does not list is refused", took is False,
          why[:80] if not took else "accepted it")

    print("\nand llm: reaches a REQUIRED question")
    # _field_for_question searched empty OPTIONAL boxes only, so every one of
    # these six - all required - came back "I could not find that box on the
    # page", which is the whole of "the llm: flow is not working".
    fresh = browser.snapshot(page)
    for typed, want in (
            ("llm: Have you ever been terminated, discharged, or asked to resign",
             "Have you ever been terminated"),
            ("llm : Have you ever been employed by USP?", "employed by USP"),
            ("ai: what are your salary expectations", "salary expectations")):
        found = worker._field_for_question(
            fresh, worker._llm_instruction(typed) or "")
        check(f"{typed[:34]!r} finds its box",
              found is not None and want in str(found.get("label") or ""),
              repr(str(found.get("label") or "")[:50]) if found else "nothing")

    print("\nand a required question is never reported as 'left empty (optional)'")
    spare = [str(f.get("label") or "") for f in worker._unanswered_questions(fresh)]
    check("no required question is in that list",
          not any(q[:24] in " | ".join(spare) for q in QUESTIONS), str(spare)[:90])

    b.close()
TMP.cleanup()

print()
if failures:
    print("LONG RADIO CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("LONG RADIO CHECK PASSED")
